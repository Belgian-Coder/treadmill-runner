#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path

TESTS = [
    Path("excel/run_self_tests.py"),
    Path("word/run_self_tests.py"),
    Path("powerpoint/run_self_tests.py"),
    Path("pdf/run_self_tests.py"),
    Path("markdown/run_self_tests.py"),
]


def test_dispatcher_pdf_workflow_help(root: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-B", str(root / "document_artifacts.py"), "pdf-workflow", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        print(completed.stderr or completed.stdout)
        return completed.returncode
    if "bundle-evidence" not in completed.stdout or "batch" not in completed.stdout:
        print("pdf-workflow help did not expose PDF evidence workflow commands")
        return 1
    print("PASS test_dispatcher_pdf_workflow_help")
    return 0


def test_dispatcher_format_discovery(root: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-B", str(root / "document_artifacts.py"), "formats", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        print(completed.stderr or completed.stdout)
        return completed.returncode
    report = __import__("json").loads(completed.stdout)
    if report.get("portable_dispatcher") is not True:
        print("dispatcher discovery did not identify the portable dispatcher boundary")
        return 1
    if report.get("inventory_kind") != "static-command-contract" or report.get("runtime_availability") != "not-probed":
        print("dispatcher discovery overstated runtime capability availability")
        return 1
    formats = {item["id"]: item for item in report["formats"]}
    if set(formats) != {"excel", "word", "powerpoint", "pdf", "markdown"}:
        print("dispatcher format discovery returned an unexpected format set")
        return 1
    if formats["markdown"]["operations"] != ["scan"]:
        print("dispatcher format discovery omitted Markdown security scanning")
        return 1
    if "validate" in formats["excel"]["operations"] or "validate" not in formats["pdf"]["operations"]:
        print("dispatcher format discovery does not match format-specific parsers")
        return 1
    for format_id, spec in formats.items():
        for operation in spec["operations"]:
            help_result = subprocess.run(
                [sys.executable, "-B", str(root / "document_artifacts.py"), format_id, operation, "--help"],
                check=False,
                capture_output=True,
                text=True,
            )
            if help_result.returncode:
                print(
                    f"dispatcher advertised unavailable operation: {format_id} {operation}\n"
                    f"{help_result.stderr or help_result.stdout}"
                )
                return help_result.returncode
    print("PASS test_dispatcher_format_discovery")
    return 0


def main():
    root = Path(__file__).resolve().parent
    dispatcher_status = test_dispatcher_pdf_workflow_help(root)
    if dispatcher_status:
        return dispatcher_status
    discovery_status = test_dispatcher_format_discovery(root)
    if discovery_status:
        return discovery_status
    for test in TESTS:
        completed = subprocess.run([sys.executable, "-B", str(root / test)], check=False)
        if completed.returncode:
            return completed.returncode
    print("document-artifacts self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
