#!/usr/bin/env python3
"""Deterministic stdlib proof for shared versus per-worker runtime resources."""

from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
import http.server
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ExclusiveHTTPServer(http.server.HTTPServer):
    allow_reuse_address = False


def _sqlite_owner(path: Path, owner: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runtime_owner (owner TEXT NOT NULL)")
        existing = connection.execute(
            "SELECT owner FROM runtime_owner ORDER BY rowid LIMIT 1"
        ).fetchone()
        if existing is None:
            connection.execute("INSERT INTO runtime_owner(owner) VALUES (?)", (owner,))
            connection.commit()
            return owner
        return str(existing[0])


def run_fixture(root: Path) -> dict[str, Any]:
    """Exercise collisions that repository worktrees do not isolate."""

    root.mkdir(parents=True, exist_ok=True)
    worktree_a = root / "worktree-a"
    worktree_b = root / "worktree-b"
    worktree_a.mkdir(exist_ok=True)
    worktree_b.mkdir(exist_ok=True)
    shared = root / "shared-runtime"
    shared.mkdir(exist_ok=True)

    shared_database = shared / "runtime.sqlite3"
    first_owner = _sqlite_owner(shared_database, "worker-a")
    second_owner = _sqlite_owner(shared_database, "worker-b")
    sqlite_collision = first_owner == second_owner == "worker-a"

    shared_environment = shared / "runtime.env"
    shared_environment.write_text("WORKER_ID=worker-a\n", encoding="utf-8", newline="\n")
    first_environment = shared_environment.read_text(encoding="utf-8")
    shared_environment.write_text("WORKER_ID=worker-b\n", encoding="utf-8", newline="\n")
    environment_file_collision = (
        first_environment != shared_environment.read_text(encoding="utf-8")
    )

    fixed_http_port_collision = False
    with ExitStack() as stack:
        first_server = _ExclusiveHTTPServer(("127.0.0.1", 0), _QuietHandler)
        stack.callback(first_server.server_close)
        fixed_port = int(first_server.server_address[1])
        try:
            second_server = _ExclusiveHTTPServer(("127.0.0.1", fixed_port), _QuietHandler)
        except OSError:
            fixed_http_port_collision = True
        else:
            stack.callback(second_server.server_close)

    isolated_database_a = worktree_a / "runtime.sqlite3"
    isolated_database_b = worktree_b / "runtime.sqlite3"
    isolated_owner_a = _sqlite_owner(isolated_database_a, "worker-a")
    isolated_owner_b = _sqlite_owner(isolated_database_b, "worker-b")
    sqlite_paths_distinct = (
        isolated_database_a != isolated_database_b
        and isolated_owner_a == "worker-a"
        and isolated_owner_b == "worker-b"
    )

    environment_a = worktree_a / "runtime.env"
    environment_b = worktree_b / "runtime.env"
    environment_a.write_text("WORKER_ID=worker-a\n", encoding="utf-8", newline="\n")
    environment_b.write_text("WORKER_ID=worker-b\n", encoding="utf-8", newline="\n")
    environment_files_distinct = (
        environment_a != environment_b
        and environment_a.read_text(encoding="utf-8") != environment_b.read_text(encoding="utf-8")
    )

    with ExitStack() as stack:
        server_a = _ExclusiveHTTPServer(("127.0.0.1", 0), _QuietHandler)
        stack.callback(server_a.server_close)
        server_b = _ExclusiveHTTPServer(("127.0.0.1", 0), _QuietHandler)
        stack.callback(server_b.server_close)
        allocated_ports_distinct = server_a.server_address[1] != server_b.server_address[1]

    shared_runtime = {
        "sqlite_collision": sqlite_collision,
        "fixed_http_port_collision": fixed_http_port_collision,
        "environment_file_collision": environment_file_collision,
        "worktrees_alone_are_insufficient": all(
            (sqlite_collision, fixed_http_port_collision, environment_file_collision)
        ),
    }
    isolated_runtime = {
        "sqlite_paths_distinct": sqlite_paths_distinct,
        "allocated_ports_distinct": allocated_ports_distinct,
        "environment_files_distinct": environment_files_distinct,
        "passed": all(
            (sqlite_paths_distinct, allocated_ports_distinct, environment_files_distinct)
        ),
    }
    return {
        "schema_version": 1,
        "tool": "workflow-manager.parallel-safety-fixture",
        "ok": shared_runtime["worktrees_alone_are_insufficient"]
        and isolated_runtime["passed"],
        "status": "passed"
        if shared_runtime["worktrees_alone_are_insufficient"]
        and isolated_runtime["passed"]
        else "failed",
        "shared_runtime": shared_runtime,
        "isolated_runtime": isolated_runtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.root is not None:
        report = run_fixture(args.root)
    else:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_fixture(Path(temporary))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
