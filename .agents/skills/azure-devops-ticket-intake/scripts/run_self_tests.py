#!/usr/bin/env python3
"""Self-tests for azure-devops-ticket-intake."""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import import_azure_devops_work_item as intake
import summarize_imported_ticket as summarize


class IntakeTests(unittest.TestCase):
    def make_import_folder(self, root: Path, *, with_attachment: bool = False) -> Path:
        folder = root / "US-101-Add-search"
        folder.mkdir(parents=True)
        attachment_records: list[dict[str, object]] = []
        if with_attachment:
            attachments_dir = folder / "attachments"
            attachments_dir.mkdir()
            data = b"image-bytes"
            attachment_file = attachments_dir / "diagram.png"
            attachment_file.write_bytes(data)
            attachment_records = [
                {
                    "name": "diagram.png",
                    "relative_path": "attachments/diagram.png",
                    "source_url": "https://example.invalid/diagram.png",
                    "description": "diagram",
                    "copied": True,
                    "size_bytes": len(data),
                    "sha256": intake.hashlib.sha256(data).hexdigest(),
                }
            ]
            (attachments_dir / "manifest.json").write_text(
                json.dumps({"attachments": attachment_records}, indent=2) + "\n",
                encoding="utf-8",
            )
        fields = {
            "System.Id": 101,
            "System.WorkItemType": "User Story",
            "System.Title": "Add search",
            "System.State": "New",
        }
        (folder / "ticket-info.md").write_text("# User Story Intake\n\nAdd search\n", encoding="utf-8")
        (folder / "fields.json").write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")
        (folder / "relations.json").write_text(json.dumps([], indent=2) + "\n", encoding="utf-8")
        (folder / "comments.json").write_text(json.dumps([], indent=2) + "\n", encoding="utf-8")
        (folder / "intake.json").write_text(
            json.dumps(
                {
                    "source": "fixture",
                    "work_item_id": "101",
                    "work_item_type": "story",
                    "title": "Add search",
                    "fields": fields,
                    "relations": [],
                    "comments": [],
                    "attachments": attachment_records,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return folder

    def test_summary_complete_import_is_stable_and_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = self.make_import_folder(Path(temp))
            first = summarize.build_report(folder)
            second = summarize.build_report(folder)
            self.assertEqual(first, second)
            self.assertTrue(first["ok"])
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["ticket"]["work_item_id"], "101")
            self.assertEqual(first["counts"]["fields"], 4)
            markdown = summarize.render_markdown(first)
            self.assertIn("Status: complete", markdown)
            self.assertIn("All deterministic checks passed", markdown)

    def test_summary_missing_files_reports_partial_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "partial"
            folder.mkdir()
            (folder / "ticket-info.md").write_text("# Partial\n", encoding="utf-8")
            report = summarize.build_report(folder)
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "partial")
            self.assertIn("intake.json", report["summary"]["missing_required_files"])
            self.assertTrue(any(check["id"] == "file:intake.json" for check in report["checks"]))

    def test_summary_exposes_attachment_manifest_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = self.make_import_folder(Path(temp), with_attachment=True)
            report = summarize.build_report(folder)
            self.assertTrue(report["ok"])
            self.assertEqual(report["counts"]["attachments"], 1)
            attachment = report["attachments"][0]
            self.assertEqual(attachment["relative_path"], "attachments/diagram.png")
            self.assertEqual(attachment["type"], "image")
            self.assertTrue(any("vision describe" in item for item in attachment["suggested_follow_up_commands"]))
            self.assertRegex(attachment["sha256"], r"^[0-9a-f]{64}$")
            manifest = next(item for item in report["files"] if item["path"] == "attachments/manifest.json")
            self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")
            markdown = summarize.render_markdown(report)
            self.assertIn("sha256=", markdown)

    def test_summary_writes_only_explicit_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            folder = self.make_import_folder(temp_path / "Runs")
            before = sorted(path.relative_to(temp_path).as_posix() for path in temp_path.rglob("*"))
            report = summarize.build_report(folder)
            summarize.render_markdown(report)
            after_read_only = sorted(path.relative_to(temp_path).as_posix() for path in temp_path.rglob("*"))
            self.assertEqual(before, after_read_only)

            json_out = temp_path / "evidence" / "summary.json"
            markdown_out = temp_path / "evidence" / "summary.md"
            code = summarize.main(
                [
                    str(folder),
                    "--format",
                    "json",
                    "--output-json",
                    str(json_out),
                    "--output-markdown",
                    str(markdown_out),
                ]
            )
            self.assertEqual(code, 0)
            after_outputs = sorted(path.relative_to(temp_path).as_posix() for path in temp_path.rglob("*"))
            added = sorted(set(after_outputs) - set(before))
            self.assertEqual(added, ["evidence", "evidence/summary.json", "evidence/summary.md"])

    def test_rest_import_auto_detects_type_and_downloads_attachments(self) -> None:
        original_get = intake.ado_get_json
        original_pages = intake.ado_get_json_pages
        original_download = intake.ado_download
        attachment_url = "https://dev.azure.com/example/project/_apis/wit/attachments/file-guid?fileName=diagram.png"
        calls: list[str] = []

        def fake_get(url: str, pat: str) -> dict[str, object]:
            self.assertEqual(pat, "pat-value")
            calls.append(url)
            if "/comments?" in url:
                return {
                    "comments": [
                        {
                            "text": "<p>Confirmed by QA</p>",
                            "createdBy": {"displayName": "Ada"},
                        }
                    ]
                }
            return {
                "id": 101,
                "fields": {
                    "System.Id": 101,
                    "System.WorkItemType": "Bug",
                    "System.Title": "Login screenshot fails",
                    "System.Description": (
                        '<p>See screenshot <img alt="login" '
                        f'src="{attachment_url}&download=true"></p>'
                    ),
                    "Custom.Priority": "High",
                },
                "relations": [
                    {
                        "rel": "AttachedFile",
                        "url": attachment_url,
                        "attributes": {"name": "diagram.png", "comment": "login screenshot"},
                    },
                    {
                        "rel": "System.LinkTypes.Related",
                        "url": "https://dev.azure.com/example/project/_apis/wit/workItems/100",
                    },
                ],
            }

        def fake_pages(url: str, pat: str, item_key: str) -> list[dict[str, object]]:
            self.assertEqual(item_key, "comments")
            self.assertEqual(pat, "pat-value")
            calls.append(url)
            return [
                {
                    "text": "<p>Confirmed by QA</p>",
                    "createdBy": {"displayName": "Ada"},
                }
            ]

        def fake_download(url: str, pat: str, max_bytes: int) -> bytes:
            self.assertEqual(url, attachment_url)
            self.assertEqual(pat, "pat-value")
            self.assertGreater(max_bytes, 10)
            return b"png-bytes"

        intake.ado_get_json = fake_get
        intake.ado_get_json_pages = fake_pages
        intake.ado_download = fake_download
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "Runs"
                code = intake.main(
                    [
                        "--work-item-id",
                        "101",
                        "--organization-url",
                        "https://dev.azure.com/example",
                        "--project",
                        "project",
                        "--pat",
                        "pat-value",
                        "--output-root",
                        str(root),
                        "--include-comments",
                    ]
                )
                self.assertEqual(code, 0)
                folder = next(root.iterdir())
                self.assertTrue((folder / "attachments" / "diagram.png").exists())
                self.assertTrue((folder / "fields.json").exists())
                self.assertTrue((folder / "relations.json").exists())
                self.assertTrue((folder / "comments.json").exists())

                ticket_info = (folder / "ticket-info.md").read_text(encoding="utf-8")
                self.assertIn('src="attachments/diagram.png"', ticket_info)
                self.assertNotIn("dev.azure.com/example", ticket_info)

                payload = json.loads((folder / "intake.json").read_text(encoding="utf-8"))
                self.assertEqual(payload["source"], "azure-devops")
                self.assertEqual(payload["work_item_type"], "bug")
                self.assertEqual(payload["fields"]["Custom.Priority"], "High")
                self.assertEqual(payload["relations"][0]["rel"], "AttachedFile")
                self.assertEqual(payload["attachments"][0]["relative_path"], "attachments/diagram.png")
                self.assertEqual(payload["attachments"][0]["type"], "image")
                self.assertEqual(payload["attachments"][0]["sha256"], intake.hashlib.sha256(b"png-bytes").hexdigest())
                self.assertNotIn("pat-value", json.dumps(payload))
                self.assertTrue(any("$expand=all" in call.lower() for call in calls))
        finally:
            intake.ado_get_json = original_get
            intake.ado_get_json_pages = original_pages
            intake.ado_download = original_download

    def test_comments_pagination_uses_continuation_token(self) -> None:
        original_page = intake.ado_get_json_page
        seen_urls: list[str] = []

        def fake_page(url: str, pat: str) -> tuple[dict[str, object], dict[str, str]]:
            self.assertEqual(pat, "pat-value")
            seen_urls.append(url)
            if len(seen_urls) == 1:
                return {"comments": [{"text": "first"}]}, {"x-ms-continuationtoken": "next-token"}
            return {"comments": [{"text": "second"}]}, {}

        intake.ado_get_json_page = fake_page
        try:
            comments = intake.ado_get_json_pages(
                "https://dev.azure.com/example/project/_apis/wit/workItems/1/comments?api-version=7.1-preview.4",
                "pat-value",
                "comments",
            )
        finally:
            intake.ado_get_json_page = original_page
        self.assertEqual([item["text"] for item in comments], ["first", "second"])
        self.assertIn("continuationToken=next-token", seen_urls[1])

    def test_rest_type_override_conflict_fails(self) -> None:
        original_get = intake.ado_get_json

        def fake_get(url: str, pat: str) -> dict[str, object]:
            return {
                "id": 202,
                "fields": {
                    "System.WorkItemType": "Bug",
                    "System.Title": "Conflict",
                },
                "relations": [],
            }

        intake.ado_get_json = fake_get
        try:
            with tempfile.TemporaryDirectory() as temp:
                code = intake.main(
                    [
                        "--work-item-id",
                        "202",
                        "--work-item-type",
                        "story",
                        "--organization-url",
                        "https://dev.azure.com/example",
                        "--project",
                        "project",
                        "--pat",
                        "pat-value",
                        "--output-root",
                        temp,
                    ]
                )
                self.assertEqual(code, 1)
        finally:
            intake.ado_get_json = original_get

    def test_rest_requires_pat_or_environment_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                code = intake.main(
                    [
                        "--work-item-id",
                        "303",
                        "--organization-url",
                        "https://dev.azure.com/example",
                        "--project",
                        "project",
                        "--output-root",
                        temp,
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 1)
            payload = json.loads(output.getvalue())
            self.assertIn("credential_setup", payload)
            self.assertEqual(payload["credential_setup"]["service"], "azure-devops")
            self.assertIn("pat-env-or-pat", payload["credential_setup"]["missing"])
            self.assertIn("credential-doctor --configure", payload["credential_setup"]["configure_command"])

    def test_missing_server_profile_reports_configure_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                code = intake.main(
                    [
                        "--server-name",
                        "customer-a",
                        "--secrets-file",
                        str(Path(temp) / "missing.json"),
                        "--work-item-id",
                        "303",
                        "--output-root",
                        temp,
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["credential_setup"]["missing"], ["profile:customer-a"])
            self.assertIn("--name customer-a", payload["credential_setup"]["configure_command"])

    def test_manual_intake_without_service_config_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            code = intake.main(
                [
                    "--work-item-id",
                    "303",
                    "--work-item-type",
                    "task",
                    "--title",
                    "Manual task",
                    "--output-root",
                    temp,
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0)

    def test_description_image_rewrite_uses_downloaded_attachment_paths(self) -> None:
        manifest = [
            {
                "name": "first image.png",
                "source_url": "https://example.invalid/attachment?id=1",
                "relative_path": "attachments/first-image.png",
            }
        ]
        description = '<p><img src="https://example.invalid/attachment?id=1&download=true"></p>'
        rewritten = intake.rewrite_description_image_sources(description, manifest)
        self.assertEqual(rewritten, '<p><img src="attachments/first-image.png"></p>')

    def test_manual_story_intake_is_created_inside_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Runs"
            code = intake.main(
                [
                    "--work-item-type",
                    "story",
                    "--title",
                    "../Create searchable customers",
                    "--output-root",
                    str(root),
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0)
            folders = list(root.iterdir())
            self.assertEqual(len(folders), 1)
            self.assertTrue((folders[0] / "ticket-info.md").exists())
            self.assertIn(root.resolve(), folders[0].resolve().parents)

    def test_workflow_root_blocks_output_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            workflow_root = temp_path / "user-story-workflow"
            outside_runs = temp_path / "other-runs"
            code = intake.main(
                [
                    "--work-item-id",
                    "123",
                    "--work-item-type",
                    "story",
                    "--title",
                    "Escaping output",
                    "--output-root",
                    str(outside_runs),
                    "--workflow-root",
                    str(workflow_root),
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 1)
            self.assertFalse(outside_runs.exists())

    def test_workflow_root_allows_runs_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workflow_root = Path(temp) / "user-story-workflow"
            runs_root = workflow_root / "runs"
            code = intake.main(
                [
                    "--work-item-id",
                    "123",
                    "--work-item-type",
                    "story",
                    "--title",
                    "Allowed output",
                    "--output-root",
                    str(runs_root),
                    "--workflow-root",
                    str(workflow_root),
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(runs_root.exists())

    def test_fixture_bug_with_local_attachment_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            attachment = temp_path / "trace.txt"
            attachment.write_text("stack trace", encoding="utf-8")
            fixture = temp_path / "bug.json"
            fixture.write_text(
                json.dumps(
                    {
                        "id": 42,
                        "fields": {
                            "System.WorkItemType": "Bug",
                            "System.Title": "Checkout throws 500",
                            "System.Description": "<p>Fails on payment</p>",
                            "Microsoft.VSTS.TCM.ReproSteps": "Open checkout",
                        },
                        "relations": [
                            {
                                "rel": "AttachedFile",
                                "url": "https://example.invalid/trace.txt",
                                "attributes": {"name": "trace.txt"},
                                "local_path": "trace.txt",
                            }
                        ],
                        "comments": [{"text": "<p>Observed in release branch</p>"}],
                    }
                ),
                encoding="utf-8",
            )
            root = temp_path / "Runs"
            code = intake.main(
                [
                    "--fixture-json",
                    str(fixture),
                    "--work-item-type",
                    "bug",
                    "--output-root",
                    str(root),
                    "--include-comments",
                    "--include-attachments",
                ]
            )
            self.assertEqual(code, 0)
            folder = next(root.iterdir())
            manifest = json.loads((folder / "attachments" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["attachments"][0]["name"], "trace.txt")
            self.assertEqual(manifest["attachments"][0]["description"], "trace.txt")
            self.assertEqual(manifest["attachments"][0]["type"], "text-log")
            self.assertTrue(any("inventory-summary" in item for item in manifest["attachments"][0]["suggested_follow_up_commands"]))
            self.assertTrue((folder / "attachments" / "trace.txt").exists())

    def test_attachment_type_classification_and_follow_up_commands(self) -> None:
        cases = {
            "design.pdf": ("pdf", "pdf_tools.py"),
            "notes.docx": ("word-document", "word_tools.py"),
            "sheet.xlsx": ("spreadsheet", "excel_tools.py"),
            "deck.pptx": ("presentation", "powerpoint_tools.py"),
            "screen.jpeg": ("image", "vision describe"),
            "trace.har": ("trace", "validate_local_quality.py"),
            "archive.zip": ("archive", "dotnet-security-review"),
        }
        for filename, (kind, command_fragment) in cases.items():
            self.assertEqual(intake.attachment_type(filename), kind)
            commands = intake.attachment_follow_up_commands(filename, f"attachments/{filename}")
            if command_fragment:
                self.assertTrue(any(command_fragment in command for command in commands), filename)

    def test_unsupported_type_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            code = intake.main(["--work-item-type", "issue", "--title", "x", "--output-root", temp])
            self.assertEqual(code, 0)
            self.assertEqual(intake.normalize_type("task"), "task")
            self.assertEqual(intake.normalize_type("feature"), "feature")
            self.assertEqual(intake.normalize_type("epic"), "epic")
            with self.assertRaises(ValueError):
                intake.normalize_type("incident")

    def test_server_profile_uses_gitignored_secrets_shape(self) -> None:
        original_get = intake.ado_get_json
        seen: dict[str, str] = {}

        def fake_get(url: str, pat: str) -> dict[str, object]:
            seen["url"] = url
            seen["pat"] = pat
            return {
                "id": 404,
                "fields": {
                    "System.Id": 404,
                    "System.WorkItemType": "Task",
                    "System.Title": "Profile import",
                },
                "relations": [],
            }

        intake.ado_get_json = fake_get
        try:
            with tempfile.TemporaryDirectory() as temp:
                temp_path = Path(temp)
                secrets = temp_path / ".agents" / "local-ai" / "secrets.local.json"
                secrets.parent.mkdir(parents=True)
                secrets.write_text(
                    json.dumps(
                        {
                            "azure_devops": [
                                {
                                    "name": "customer-a",
                                    "server_url": "https://dev.azure.com/customer-a",
                                    "project": "ProjectA",
                                    "pat": "profile-pat",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                code = intake.main(
                    [
                        "--server-name",
                        "customer-a",
                        "--secrets-file",
                        str(secrets),
                        "--work-item-id",
                        "404",
                        "--output-root",
                        str(temp_path / "Runs"),
                    ]
                )
                self.assertEqual(code, 0)
                self.assertEqual(seen["pat"], "profile-pat")
                self.assertIn("ProjectA", seen["url"])
                folder = next((temp_path / "Runs").iterdir())
                payload = json.loads((folder / "intake.json").read_text(encoding="utf-8"))
                self.assertEqual(payload["work_item_type"], "task")
        finally:
            intake.ado_get_json = original_get

    def test_dry_run_and_duplicate_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Runs"
            code = intake.main(
                [
                    "--work-item-id",
                    "123",
                    "--work-item-type",
                    "story",
                    "--title",
                    "Add search",
                    "--output-root",
                    str(root),
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(root.exists())

            self.assertEqual(
                intake.main(
                    [
                        "--work-item-id",
                        "123",
                        "--work-item-type",
                        "story",
                        "--title",
                        "Add search",
                        "--output-root",
                        str(root),
                    ]
                ),
                0,
            )
            duplicate = intake.main(
                [
                    "--work-item-id",
                    "123",
                    "--work-item-type",
                    "story",
                    "--title",
                    "Add search again",
                    "--output-root",
                    str(root),
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(duplicate, 1)

    def test_raw_source_redacts_credential_like_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fixture = temp_path / "story.json"
            fixture.write_text(
                json.dumps(
                    {
                        "id": 7,
                        "fields": {
                            "System.WorkItemType": "User Story",
                            "System.Title": "Credential fixture",
                            "System.Description": "Token abc",
                            "Custom.Token": "secret-token-value",
                        },
                    }
                ),
                encoding="utf-8",
            )
            root = temp_path / "Runs"
            self.assertEqual(
                intake.main(
                    [
                        "--fixture-json",
                        str(fixture),
                        "--output-root",
                        str(root),
                        "--include-raw-source",
                    ]
                ),
                0,
            )
            payload = json.loads((next(root.iterdir()) / "intake.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["raw_source"]["fields"]["Custom.Token"], "<redacted>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
