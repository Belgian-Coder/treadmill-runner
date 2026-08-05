#!/usr/bin/env python3
"""Public CLI/import wrapper for dotnet-project-context."""

from __future__ import annotations

import sys

from dotnet_context_support.implementation import (  # noqa: F401
    SCHEMA_VERSION,
    TOOL_ID,
    Runner,
    assert_safe_dotnet_command,
    build_parser,
    build_report,
    ci_report,
    cli_probe_report,
    collect_package_names,
    configuration_report,
    context_facts,
    default_runner,
    diff_reports,
    features_report,
    iter_files,
    main,
    nuget_report,
    parse_dotnet_version,
    parse_global_json,
    parse_key_value_lines,
    parse_nuget_config,
    parse_project,
    parse_repeated_key_value_lines,
    parse_solution,
    persistence_report,
    project_graph_report,
    redact_source_value,
    render_markdown,
    resolve_output_dir,
    restore_prerequisites_report,
    run_safe_dotnet,
    validation_candidates,
    write_evidence,
)

sys.dont_write_bytecode = True


if __name__ == "__main__":
    raise SystemExit(main())
