#!/usr/bin/env python3
"""Self-tests for external-reference-manager."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sync_references


def git_available() -> bool:
    return shutil.which("git") is not None


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class ReferenceOnboardingTests(unittest.TestCase):
    def test_skill_docs_name_read_only_and_write_boundaries(self) -> None:
        skill = Path(__file__).resolve().parents[1] / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertIn("Read-Only Dogfood", text)
        self.assertIn("Report mode is read-only", text)
        self.assertIn("Dry-run mode does not fetch or write", text)
        self.assertIn("Self-tests use temporary Git fixtures", text)
        self.assertIn("module.json.strict_read_only_commands", text)
        self.assertIn("inspect eval suites without executing them", text)
        self.assertIn("dry-run mode with `--no-fetch`", text)
        self.assertIn("source-reviewed direct script commands", text)
        self.assertIn("Manifest creation from the example is always a write", text)
        self.assertIn("missing active manifest is a valid skipped terminal state", text)
        self.assertIn("Run self-tests only when temporary Git fixture writes are allowed", text)
        self.assertIn("Creating the manifest from the example is write-capable", text)
        self.assertIn("Skip `--write`", text)
        self.assertIn("caller-owned workflow or project evidence", text)
        self.assertIn("non-blocking", text)
        self.assertNotIn("cleanup intent", text)

    def test_cli_help_explains_read_only_and_write_boundaries(self) -> None:
        script = Path(__file__).resolve().parent / "sync_references.py"
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "--help"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        normalized = completed.stdout.replace("-\n", "-").replace("\n", " ")
        self.assertIn("Report, dry-run, or refresh declared Git references", normalized)
        self.assertIn("Default report mode is read-only", normalized)
        self.assertIn("Dry-run mode does not fetch or write", normalized)
        self.assertIn("--write is write-capable", normalized)

    def test_cli_rejects_write_and_dry_run_together(self) -> None:
        script = Path(__file__).resolve().parent / "sync_references.py"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--manifest",
                "manifest.json",
                "--output-root",
                "references",
                "--dry-run",
                "--write",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("not allowed with argument", completed.stderr)

    def test_report_mode_missing_manifest_returns_skipped_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            manifest = workspace / "automations" / "reference-refresh" / "artifacts" / "references" / "reference-manifest.json"
            manifest.parent.mkdir(parents=True)
            example = manifest.with_name("reference-manifest.example.json")
            example.write_text('{"references":[]}\n', encoding="utf-8")
            result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(manifest.parent),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": False,
                    },
                )()
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "missing-active-manifest")
            self.assertEqual(result["example_manifest"], str(example))
            self.assertIn("copy", result["next_command"])
            self.assertIn("When workspace writes are approved", result["next_command"])
            self.assertIn("strict read-only/no-write dogfood", result["next_command"])
            self.assertIn("missing-active-manifest", sync_references.render_text(result))
            self.assertIn("## Skipped", sync_references.render_markdown(result))


@unittest.skipUnless(git_available(), "git is required")
class ReferenceTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "source"
        repo.mkdir()
        run_git(["init", "-b", "main"], repo)
        run_git(["config", "user.email", "test@example.invalid"], repo)
        run_git(["config", "user.name", "Test User"], repo)
        (repo / "README.md").write_text("# Reference\n", encoding="utf-8")
        run_git(["add", "README.md"], repo)
        run_git(["commit", "-m", "initial"], repo)
        return repo

    def test_refresh_local_reference_writes_pin_and_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": "repositories/sample",
                                "branch": "main",
                                "purpose": "Fixture reference.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            code = sync_references.main(
                [
                    "--manifest",
                    str(manifest),
                    "--output-root",
                    str(workspace / "References"),
                    "--workspace-root",
                    str(workspace),
                    "--write",
                ]
            )
            self.assertEqual(code, 0)
            pinned_path = workspace / "References" / "pinned-references.json"
            card_path = workspace / "References" / "cards" / "sample.md"
            self.assertTrue(pinned_path.exists())
            self.assertTrue(card_path.exists())
            pinned = json.loads(pinned_path.read_text(encoding="utf-8"))
            reference = pinned["pinned_references"][0]
            self.assertEqual(reference["card_sha256"], hashlib.sha256(card_path.read_bytes()).hexdigest())
            self.assertEqual(reference["card_integrity"]["status"], "recorded")
            self.assertEqual(reference["card_integrity"]["algorithm"], "sha256")

    def test_report_detects_tampered_reference_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": "repositories/sample",
                                "branch": "main",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_root = workspace / "References"
            write_result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(output_root),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": True,
                    },
                )()
            )
            card_path = Path(write_result["pinned_references"][0]["card"])
            card_path.write_text(card_path.read_text(encoding="utf-8") + "\nTampered\n", encoding="utf-8")

            report = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(output_root),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": True,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": False,
                    },
                )()
            )

            reference = report["references"][0]
            self.assertEqual(reference["card_integrity"]["status"], "mismatch")
            self.assertTrue(any("card integrity mismatch" in item for item in reference["conflicts"]))

    def test_dirty_reference_is_blocked_without_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            destination = workspace / "References" / "repositories" / "sample"
            run_git(["clone", str(source), str(destination)], workspace)
            (destination / "dirty.txt").write_text("dirty", encoding="utf-8")
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": str(destination),
                                "branch": "main",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            code = sync_references.main(
                [
                    "--manifest",
                    str(manifest),
                    "--output-root",
                    str(workspace / "References"),
                    "--workspace-root",
                    str(workspace),
                    "--no-fetch",
                    "--write",
                ]
            )
            self.assertEqual(code, 1)

    def test_dry_run_reports_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": "repositories/sample",
                                "branch": "main",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(workspace / "References"),
                        "workspace_root": str(workspace),
                        "dry_run": True,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": False,
                    },
                )()
            )
            self.assertEqual(result["status"], "dry-run")
            self.assertEqual(result["planned_changes"][0]["action"], "clone")
            self.assertFalse((workspace / "References").exists())

    def test_changed_since_last_pin_and_missing_referenced_file_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            first_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            (source / "README.md").write_text("# Reference\n\nChanged\n", encoding="utf-8")
            run_git(["add", "README.md"], source)
            run_git(["commit", "-m", "second"], source)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "pinned_references": [{"name": "sample", "commit": first_commit}],
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": "repositories/sample",
                                "branch": "main",
                                "referenced_files": ["missing-file.md"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(workspace / "References"),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": -1,
                        "write": True,
                    },
                )()
            )
            ref = result["pinned_references"][0]
            self.assertTrue(ref["changed"])
            self.assertTrue(ref["changed_since_last_pin"])
            self.assertTrue(ref["stale_reference"]["stale_by_age"])
            self.assertTrue(any("missing" in item for item in ref["conflicts"]))

    def test_credential_url_is_redacted_and_warned(self) -> None:
        url = "https://user:secret@dev.azure.com/org/project/_git/repo"
        self.assertEqual(
            sync_references.redacted_url(url),
            "https://dev.azure.com/org/project/_git/repo",
        )
        warnings = sync_references.credential_boundary_warnings(url)
        self.assertTrue(any("inline credentials" in item for item in warnings))

    def test_report_only_default_json_shape_and_no_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            destination = workspace / "References" / "repositories" / "sample"
            run_git(["clone", str(source), str(destination)], workspace)
            first_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=destination,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "pinned_references": [{"name": "sample", "commit": first_commit}],
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": str(destination),
                                "branch": "main",
                                "referenced_files": ["README.md"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(workspace / "References"),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": False,
                    },
                )()
            )
            self.assertEqual(result["schema_version"], 2)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "report-only")
            self.assertEqual(result["summary"]["reference_count"], 1)
            self.assertIn("references", result)
            self.assertFalse((workspace / "References" / "cards").exists())
            self.assertFalse((workspace / "References" / "pinned-references.json").exists())

    def test_report_detects_deleted_and_renamed_referenced_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = self.make_repo(workspace)
            (source / "old-name.txt").write_text("tracked\n", encoding="utf-8")
            (source / "delete-me.txt").write_text("tracked\n", encoding="utf-8")
            run_git(["add", "old-name.txt", "delete-me.txt"], source)
            run_git(["commit", "-m", "add tracked files"], source)
            previous_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            run_git(["mv", "old-name.txt", "new-name.txt"], source)
            (source / "delete-me.txt").unlink()
            run_git(["add", "-A"], source)
            run_git(["commit", "-m", "rename and delete files"], source)
            destination = workspace / "References" / "repositories" / "sample"
            run_git(["clone", str(source), str(destination)], workspace)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "pinned_references": [{"name": "sample", "commit": previous_commit}],
                        "references": [
                            {
                                "name": "sample",
                                "repository_url": str(source),
                                "path": str(destination),
                                "branch": "main",
                                "referenced_files": ["old-name.txt", "delete-me.txt"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = sync_references.sync(
                type(
                    "Args",
                    (),
                    {
                        "manifest": str(manifest),
                        "output_root": str(workspace / "References"),
                        "workspace_root": str(workspace),
                        "dry_run": False,
                        "no_fetch": False,
                        "allow_reset": False,
                        "stale_days": 180,
                        "write": False,
                    },
                )()
            )
            signals = result["references"][0]["file_signals"]
            self.assertTrue(signals["deleted"])
            self.assertTrue(signals["renamed"])
            conflicts = result["references"][0]["conflicts"]
            self.assertTrue(any("deleted since last pin" in item for item in conflicts))
            self.assertTrue(any("renamed since last pin" in item for item in conflicts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
