#!/usr/bin/env python3
"""Self-tests for sonarqube-diagnostics."""

from __future__ import annotations

import json
import io
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout

import compare_coverage
import export_issues
import run_analysis
import sonarqube_client


class SonarDiagnosticsTests(unittest.TestCase):
    def test_compare_coverage_within_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root / "local.json"
            remote = root / "remote.json"
            local.write_text(json.dumps({"coverage_percent": 81.5}), encoding="utf-8")
            remote.write_text(json.dumps({"measures": [{"metric": "coverage", "value": "81.0"}]}), encoding="utf-8")
            result = compare_coverage.compare(local, remote, tolerance=1.0)
            self.assertEqual(result["schema_version"], 1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["delta"], 0.5)

    def test_compare_coverage_outside_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root / "local.json"
            remote = root / "remote.json"
            local.write_text(json.dumps({"coverage_percent": 70}), encoding="utf-8")
            remote.write_text(json.dumps({"measures": [{"metric": "coverage", "value": "80"}]}), encoding="utf-8")
            result = compare_coverage.compare(local, remote, tolerance=1.0)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")

    def test_export_issue_failure_writes_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "issues.json"
            with mock.patch.object(export_issues, "SonarClient", side_effect=RuntimeError("network unavailable")):
                with redirect_stdout(io.StringIO()):
                    exit_code = export_issues.main(
                        [
                            "--base-url",
                            "https://sonar.invalid",
                            "--project-key",
                            "demo",
                            "--token",
                            "token-value",
                            "--output-json",
                            str(output),
                        ]
                    )
            self.assertEqual(exit_code, 1)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["project_key"], "demo")
            self.assertEqual(payload["error_type"], "RuntimeError")

    def test_issue_export_normalizes_severity_and_read_only_assertion(self) -> None:
        class FakeClient:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def get_json(self, *_args, **_kwargs):
                return {
                    "issues": [
                        {
                            "key": "ISSUE-1",
                            "severity": "MAJOR",
                            "type": "BUG",
                            "component": "demo:App.cs",
                            "message": "Example",
                            "line": 12,
                        }
                    ],
                    "paging": {"total": 1},
                }

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "issues.json"
            with mock.patch.object(export_issues, "SonarClient", FakeClient):
                with redirect_stdout(io.StringIO()):
                    exit_code = export_issues.main(
                        [
                            "--base-url",
                            "https://user:secret@sonar.invalid",
                            "--project-key",
                            "demo",
                            "--token",
                            "token-value",
                            "--output-json",
                            str(output),
                        ]
                    )
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["read_only"])
            self.assertTrue(payload["no_upload_assertion"])
            self.assertEqual(payload["base_url"], "https://sonar.invalid")
            self.assertEqual(payload["normalized_issues"][0]["severity"], "MAJOR")
            self.assertEqual(payload["normalized_issues"][0]["category"], "BUG")

    def test_issue_export_uses_local_sonarqube_profile(self) -> None:
        seen: dict[str, str] = {}

        class FakeClient:
            def __init__(self, base_url, token=None) -> None:
                seen["base_url"] = base_url
                seen["token"] = token

            def get_json(self, *_args, **_kwargs):
                return {"issues": [], "paging": {"total": 0}}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secrets = root / ".agents" / "local-ai" / "secrets.local.json"
            secrets.parent.mkdir(parents=True)
            secrets.write_text(
                json.dumps(
                    {
                        "sonarqube": [
                            {
                                "name": "project-a",
                                "base_url": "https://sonar.example",
                                "project_key": "ProjectA",
                                "token": "local-token",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "issues.json"
            with mock.patch.object(export_issues, "SonarClient", FakeClient):
                with redirect_stdout(io.StringIO()):
                    exit_code = export_issues.main(
                        [
                            "--server-name",
                            "project-a",
                            "--secrets-file",
                            str(secrets),
                            "--output-json",
                            str(output),
                        ]
                    )
            self.assertEqual(exit_code, 0)
            self.assertEqual(seen["base_url"], "https://sonar.example")
            self.assertEqual(seen["token"], "local-token")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["project_key"], "ProjectA")

    def test_issue_export_missing_token_reports_credential_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "issues.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = export_issues.main(
                    [
                        "--base-url",
                        "https://sonar.invalid",
                        "--project-key",
                        "demo",
                        "--output-json",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 1)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("credential_setup", payload)
            self.assertIn("token-env-or-token", payload["credential_setup"]["missing"])
            self.assertIn("credential-doctor --configure", payload["credential_setup"]["configure_command"])
            printed = json.loads(stdout.getvalue())
            self.assertIn("credential_setup", printed)

    def test_run_analysis_no_upload_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "analysis.json"
            with redirect_stdout(io.StringIO()):
                exit_code = run_analysis.main(
                    [
                        "--project-root",
                        temp,
                        "--project-key",
                        "demo",
                        "--base-url",
                        "https://sonar.invalid",
                        "--output-json",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "skipped")
            self.assertTrue(payload["no_upload_assertion"])

    def test_run_analysis_uses_local_sonarqube_profile_without_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secrets = root / ".agents" / "local-ai" / "secrets.local.json"
            secrets.parent.mkdir(parents=True)
            secrets.write_text(
                json.dumps(
                    {
                        "sonarqube": [
                            {
                                "name": "project-a",
                                "base_url": "https://sonar.example",
                                "project_key": "ProjectA",
                                "token_env": "SONAR_TOKEN",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "analysis.json"
            with redirect_stdout(io.StringIO()):
                exit_code = run_analysis.main(
                    [
                        "--project-root",
                        temp,
                        "--server-name",
                        "project-a",
                        "--secrets-file",
                        str(secrets),
                        "--output-json",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["project_key"], "ProjectA")
            self.assertEqual(payload["status"], "skipped")

    def test_redacts_urls_with_credentials(self) -> None:
        self.assertEqual(
            sonarqube_client.redacted_url("https://user:secret@sonar.example.local/path"),
            "https://sonar.example.local/path",
        )


if __name__ == "__main__":
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        argparse.ArgumentParser(description="write/temp: run SonarQube diagnostics self-tests using temporary fixture files").parse_args()
        raise SystemExit(0)
    unittest.main(verbosity=2)
