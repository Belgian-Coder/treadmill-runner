#!/usr/bin/env python3
"""Self-tests for dotnet-security-review."""

from __future__ import annotations

import tempfile
import unittest
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

sys.dont_write_bytecode = True

import scan_security_patterns
import sarif_support


class SecurityPatternTests(unittest.TestCase):
    def test_skill_docs_use_actual_target_flag(self) -> None:
        skill = Path(__file__).resolve().parents[2] / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertIn("--target <files-or-dirs>", text)
        self.assertNotIn("--paths <files-or-dirs>", text)

    def test_skill_docs_name_read_only_and_write_capable_boundaries(self) -> None:
        skill = Path(__file__).resolve().parents[2] / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertIn("Read-Only Dogfood", text)
        self.assertIn("without `--output-*`", text)
        self.assertIn("write-capable", text)
        self.assertIn("caller-owned workflow or project evidence", text)
        self.assertIn("inspect eval suites without executing them", text)
        self.assertIn("Treat `--fail-on` nonzero exits as findings status", text)
        self.assertIn("tracked changed files", text)
        self.assertIn("`--input-sarif` as local read-only input", text)
        self.assertIn("Skip self-tests/eval suites when they create temp fixtures", text)
        self.assertIn("not strict dogfood", text)
        self.assertIn("Skip local AI", text)
        self.assertIn("non-blocking", text)
        self.assertIn("credentialed risk profile means local secret-like config may be inspected", text)
        self.assertIn("intentionally skips installed harness and workflow roots", text)

    def test_dispatcher_help_explains_read_only_boundary(self) -> None:
        script = Path(__file__).resolve().parents[1] / "dotnet_security_review.py"
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "--help"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("scan --target", completed.stdout)
        self.assertIn("Read-only boundary", completed.stdout)
        self.assertIn("write-capable", completed.stdout)
        self.assertIn("may create parent directories", completed.stdout)
        self.assertIn("tracked changes", completed.stdout)
        self.assertIn("under the requested target", completed.stdout)
        self.assertIn("does not mutate source files", completed.stdout)

    def test_scanner_help_labels_output_flags_as_write_capable(self) -> None:
        script = Path(__file__).resolve().parent / "scan_security_patterns.py"
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "--help"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        normalized = completed.stdout.replace("-\n", "-").replace("\n", " ")
        self.assertIn("Read-only when no --output-* flags are set", normalized)
        self.assertIn("under the requested target", normalized)
        self.assertIn("write-capable", normalized)
        self.assertIn("may create parent directories", normalized)
        self.assertIn("local SARIF only", normalized)
        self.assertIn("caller-owned review evidence", normalized)

    def test_scan_finds_high_severity_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Controller.cs"
            path.write_text("[AllowAnonymous]\npublic IActionResult Index() => View();\n", encoding="utf-8")
            payload = scan_security_patterns.scan(
                Namespace(target=[str(Path(temp))], changed_only=False, fail_on="high", include_suppressed=False)
            )
            self.assertEqual(payload["schema_version"], 1)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["summary"]["high"], 1)
            self.assertEqual(payload["findings"][0]["confidence"], "high")
            self.assertIn("not a full security audit", payload["boundary"])

    def test_scan_passes_without_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Safe.cs"
            path.write_text("public class Safe {}\n", encoding="utf-8")
            payload = scan_security_patterns.scan(
                Namespace(target=[str(Path(temp))], changed_only=False, fail_on="high", include_suppressed=False)
            )
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["files_scanned"], 1)

    def test_language_rules_and_suppression_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app.py").write_text(
                "import pickle\npickle.loads(data)\n"
                "os.system(cmd)  # dotnet-security-review: ignore SEC010 because fixture command is constant\n",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=True)
            )
            rule_ids = {item["rule_id"] for item in payload["findings"]}
            self.assertIn("SEC009", rule_ids)
            self.assertNotIn("SEC010", rule_ids)
            self.assertEqual(payload["suppressed_findings"][0]["rule_id"], "SEC010")

    def test_skips_generated_and_binary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "safe.generated.cs").write_text("[AllowAnonymous]\n", encoding="utf-8")
            (root / "binary.cs").write_bytes(b"\0bad")
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(any("generated" in item for item in payload["skipped"]))
            self.assertTrue(any("binary" in item for item in payload["skipped"]))

    def test_scan_skips_installed_harness_roots_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = root / ".agents" / "skills" / "demo" / "fixtures" / "Unsafe.cs"
            harness.parent.mkdir(parents=True)
            harness.write_text("[AllowAnonymous]\n", encoding="utf-8")
            app = root / "src" / "Safe.cs"
            app.parent.mkdir(parents=True)
            app.write_text("public class Safe {}\n", encoding="utf-8")

            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["files_scanned"], 1)
            self.assertTrue(any(".agents" in item for item in payload["skipped"]))

    def test_changed_only_filters_to_requested_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            src = root / "src"
            tests = root / "tests"
            src.mkdir()
            tests.mkdir()
            safe_src = src / "Safe.cs"
            safe_test = tests / "UnsafeTests.cs"
            safe_src.write_text("public class Safe {}\n", encoding="utf-8")
            safe_test.write_text("public class UnsafeTests {}\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(
                ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            safe_src.write_text("[AllowAnonymous]\npublic class Safe {}\n", encoding="utf-8")
            safe_test.write_text("[AllowAnonymous]\npublic class UnsafeTests {}\n", encoding="utf-8")

            payload = scan_security_patterns.scan(
                Namespace(target=[str(src)], changed_only=True, fail_on=None, include_suppressed=False)
            )

            self.assertEqual(payload["files_scanned"], 1)
            self.assertEqual(Path(payload["findings"][0]["path"]).name, "Safe.cs")

    def test_sarif_input_and_output_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Safe.cs").write_text("public class Safe {}\n", encoding="utf-8")
            sarif = root / "input.sarif"
            sarif.write_text(
                json.dumps(
                    {
                        "version": "2.1.0",
                        "runs": [
                            {
                                "tool": {"driver": {"name": "demo-sarif"}},
                                "results": [
                                    {
                                        "ruleId": "S999",
                                        "level": "error",
                                        "message": {"text": "Imported SARIF issue."},
                                        "locations": [
                                            {
                                                "physicalLocation": {
                                                    "artifactLocation": {"uri": "Safe.cs"},
                                                    "region": {"startLine": 1},
                                                }
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = scan_security_patterns.scan(
                Namespace(
                    target=[str(root)],
                    changed_only=False,
                    fail_on="high",
                    include_suppressed=False,
                    input_sarif=[str(sarif)],
                )
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["sarif_summary"]["levels"], {"error": 1})
            self.assertTrue(any(item.get("source") == "sarif" for item in payload["findings"]))

            exported = sarif_support.sarif_from_findings(payload["findings"], "dotnet-security-review")
            self.assertEqual(exported["version"], "2.1.0")
            self.assertEqual(len(exported["runs"][0]["results"]), len(payload["findings"]))

    def test_office_zip_safety_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "office_extract.py").write_text(
                "import zipfile\n"
                "def unpack_docx(path, out):\n"
                "    zipfile.ZipFile(path).extractall(out)\n",
                encoding="utf-8",
            )
            (root / "OpenXmlHandler.cs").write_text(
                "var kind = \"docx\"; ZipArchive.ExtractToDirectory(path, output);\n",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )
            rule_ids = {item["rule_id"] for item in payload["findings"]}
            self.assertIn("SEC017", rule_ids)
            self.assertIn("SEC018", rule_ids)

    def test_dotnet_owasp_sql_and_ssrf_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "OrdersController.cs").write_text(
                """public sealed class OrdersController
{
    public async Task Fetch(FetchRequest request, HttpClient client)
    {
        var raw = db.Orders.FromSqlRaw("SELECT * FROM Orders WHERE Status = '" + request.Status + "'");
        var body = await client.GetStringAsync(request.Url);
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = {item["rule_id"] for item in payload["findings"]}
            self.assertIn("SEC019", rule_ids)
            self.assertIn("SEC020", rule_ids)
            self.assertNotIn("SEC012", rule_ids)

    def test_dotnet_deprecated_crypto_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "LegacyCrypto.cs").write_text(
                """using System.Security.Cryptography;

public sealed class LegacyCrypto
{
    public void Hash(byte[] input)
    {
        var weakHash = MD5.Create().ComputeHash(input);
        var legacySha1 = HashAlgorithmName.SHA1;
        using var des = DES.Create();
        using var tripleDes = TripleDES.Create();
        using var rc2 = RC2.Create();
        using var legacySha1Provider = new SHA1CryptoServiceProvider();
        using var legacyRng = new RNGCryptoServiceProvider();
        var strongHash = SHA256.HashData(input);
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC021"), 7)

    def test_dotnet_ecb_cipher_mode_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "EcbCrypto.cs").write_text(
                """using System.Security.Cryptography;

public sealed class EcbCrypto
{
    public void Configure()
    {
        using var aes = Aes.Create();
        aes.Mode = CipherMode.ECB;
        var mode = CipherMode.ECB;
        aes.Mode = CipherMode.CBC;
        // aes.Mode = CipherMode.ECB; documented bad example only
    }
}
""",
                encoding="utf-8",
            )
            (root / "crypto-notes.md").write_text("Avoid examples that say CipherMode.ECB in docs.\n", encoding="utf-8")
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC036"), 2)

    def test_dotnet_small_rsa_key_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SmallRsa.cs").write_text(
                """using System.Security.Cryptography;

public sealed class SmallRsa
{
    public void Configure()
    {
        using var weak = RSA.Create(1024);
        using var legacy = new RSACryptoServiceProvider(512);
        rsa.KeySize = 1024;
        using var strong = RSA.Create(2048);
        // using var documented = RSA.Create(1024);
    }
}
""",
                encoding="utf-8",
            )
            (root / "crypto-notes.md").write_text("Do not document RSA.Create(1024) as safe.\n", encoding="utf-8")
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC037"), 3)

    def test_dotnet_cors_allow_all_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "CorsSetup.cs").write_text(
                """public static class CorsSetup
{
    public static void Configure(CorsPolicyBuilder policy)
    {
        policy.AllowAnyOrigin().AllowCredentials();
        policy.SetIsOriginAllowed(_ => true).AllowCredentials();
        policy.WithOrigins("https://app.example.com").AllowCredentials();
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC022"), 2)

    def test_dotnet_cookie_secure_policy_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "CookieSetup.cs").write_text(
                """public static class CookieSetup
{
    public static void Configure(CookieAuthenticationOptions options)
    {
        options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
        options.Cookie.SecurePolicy = CookieSecurePolicy.None;
        options.Cookie.SecurePolicy = CookieSecurePolicy.Always;
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC023"), 2)

    def test_dotnet_jwt_validation_disabled_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "JwtSetup.cs").write_text(
                """public static class JwtSetup
{
    public static void Configure(JwtBearerOptions options)
    {
        options.RequireHttpsMetadata = false;
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = false,
            ValidateIssuerSigningKey = false,
            ClockSkew = TimeSpan.FromMinutes(1)
        };
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC024"), 5)

    def test_dotnet_secret_config_and_logging_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "appsettings.Production.json").write_text(
                """{
  "Smtp": {
    "ApiKey": "SG.production-key-123456"
  },
  "Jwt": {
    "SigningKey": "super-secret-signing-key-123456"
  },
  "Safe": {
    "ClientSecret": "REPLACE_VIA_ENV_OR_USER_SECRETS"
  }
}
""",
                encoding="utf-8",
            )
            (root / "SecretLogging.cs").write_text(
                """public sealed class SecretLogging
{
    public void Configure(ILogger logger, string apiKey, string connectionString)
    {
        logger.LogInformation("Using API key {ApiKey}", apiKey);
        logger.LogDebug("Connection string {ConnectionString}", connectionString);
        logger.LogInformation("API key configured: {IsConfigured}", !string.IsNullOrEmpty(apiKey));
        logger.LogInformation("Database connection configured for {Server}", serverName);
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC025"), 2)
            self.assertEqual(rule_ids.count("SEC026"), 2)

    def test_dotnet_http_body_logging_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "HttpLoggingSetup.cs").write_text(
                """public static class HttpLoggingSetup
{
    public static void Configure(HttpLoggingOptions options)
    {
        options.LoggingFields = HttpLoggingFields.All;
        options.LoggingFields = HttpLoggingFields.RequestBody
            | HttpLoggingFields.ResponseBody;
        options.LoggingFields = HttpLoggingFields.RequestPath
            | HttpLoggingFields.RequestMethod
            | HttpLoggingFields.ResponseStatusCode;
    }
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC032"), 3)

    def test_ci_nuget_credential_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                """name: ci
jobs:
  pack:
    runs-on: ubuntu-latest
    env:
      NUGET_API_KEY: nuget-live-key-123456789
      NUGET_AUTH_TOKEN: "plain-token-123456789"
      SAFE_NUGET_API_KEY: ${{ secrets.NUGET_API_KEY }}
      SAFE_NUGET_AUTH_TOKEN: $NUGET_AUTH_TOKEN
      SAFE_NUGET_TOKEN: $(NuGetToken)
      PLACEHOLDER_NUGET_API_KEY: REPLACE_WITH_SECRET
      # NUGET_API_KEY: commented-example-123456789
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC035"), 2)

    def test_dockerfile_secret_environment_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Dockerfile").write_text(
                """FROM mcr.microsoft.com/dotnet/aspnet:10.0
ENV ASPNETCORE_URLS=http://+:8080
ENV ConnectionStrings__DefaultDb=fixture-connection-value-123456
ENV Secret__Token=fixture-secret-value-123456
ENV Safe__Secret=${RUNTIME_SECRET}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC027"), 2)
            self.assertEqual(payload["files_scanned"], 1)
            self.assertFalse(any("Dockerfile: unsupported suffix" in item for item in payload["skipped"]))

    def test_dockerfile_final_sdk_image_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Dockerfile").write_text(
                """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
RUN dotnet publish -c Release -o /out

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS final
WORKDIR /app
COPY --from=build /out .
ENTRYPOINT ["dotnet", "App.dll"]
""",
                encoding="utf-8",
            )
            (root / "Containerfile").write_text(
                """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
RUN dotnet publish -c Release -o /out

FROM mcr.microsoft.com/dotnet/sdk:10.0
COPY --from=build /out .
""",
                encoding="utf-8",
            )
            (root / "Dockerfile.safe").write_text(
                """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
RUN dotnet publish -c Release -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS final
WORKDIR /app
COPY --from=build /out .
ENTRYPOINT ["dotnet", "App.dll"]
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC033"), 2)

    def test_dotnet_container_patch_pinned_image_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Dockerfile").write_text(
                """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
RUN dotnet publish -c Release -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0.1-alpine AS final
COPY --from=build /out .

FROM mcr.microsoft.com/dotnet/runtime-deps:10.0.1
FROM mcr.microsoft.com/dotnet/runtime:10.0
FROM ubuntu:22.04
# FROM mcr.microsoft.com/dotnet/aspnet:10.0.2
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC034"), 2)

    def test_dotnet_nuget_audit_weakening_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "WeakAudit.csproj").write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <NuGetAudit>false</NuGetAudit>
    <NuGetAuditMode>direct</NuGetAuditMode>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            (root / "Directory.Build.props").write_text(
                """<Project>
  <PropertyGroup>
    <NuGetAudit>true</NuGetAudit>
    <NuGetAuditMode>all</NuGetAuditMode>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC028"), 2)
            self.assertEqual(payload["files_scanned"], 2)

    def test_dotnet_binaryformatter_project_switch_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "UnsafeSerialization.csproj").write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <EnableUnsafeBinaryFormatterSerialization>true</EnableUnsafeBinaryFormatterSerialization>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            (root / "Directory.Build.props").write_text(
                """<Project>
  <PropertyGroup>
    <EnableUnsafeBinaryFormatterSerialization>false</EnableUnsafeBinaryFormatterSerialization>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="high", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC029"), 1)
            self.assertNotIn("SEC009", rule_ids)
            self.assertEqual(payload["files_scanned"], 2)

    def test_dotnet_deprecated_security_platform_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "LegacySecurity.cs").write_text(
                """using System.Security.Permissions;
using System.Runtime.Remoting;

[assembly: AllowPartiallyTrustedCallers]
[SecurityPermission(SecurityAction.Demand)]
[SecurityCritical]
public sealed class LegacyRemote : MarshalByRefObject
{
    public void Configure()
    {
        RemotingConfiguration.Configure("remoting.config", false);
    }
}
""",
                encoding="utf-8",
            )
            (root / "LegacyCom.cs").write_text(
                """using System.EnterpriseServices;

public sealed class LegacyComComponent : ServicedComponent
{
}
""",
                encoding="utf-8",
            )
            (root / "StandardAuth.cs").write_text(
                """using Microsoft.AspNetCore.Authorization;

[Authorize]
public sealed class OrdersController
{
}
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC031"), 8)
            self.assertEqual(payload["files_scanned"], 3)

    def test_nuget_config_insecure_package_source_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "NuGet.config").write_text(
                """<configuration>
  <packageSources>
    <add key="external-http" value="http://packages.example.com/v3/index.json" />
    <add key="explicit-insecure" value="https://pkgs.example.com/nuget/v3/index.json" allowInsecureConnections="true" />
    <add key="local-dev" value="http://localhost:8080/v3/index.json" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
</configuration>
""",
                encoding="utf-8",
            )
            payload = scan_security_patterns.scan(
                Namespace(target=[str(root)], changed_only=False, fail_on="medium", include_suppressed=False)
            )

            rule_ids = [item["rule_id"] for item in payload["findings"]]
            self.assertEqual(rule_ids.count("SEC030"), 2)
            self.assertEqual(payload["files_scanned"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
