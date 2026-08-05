---
title: No Python Or No Admin
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-06-10
---

# No Python Or No Admin

The harness needs a Python 3.12+ interpreter before any `.agents/manage.py` command can run. If `python`, `py`, and `python3` are all missing, setup cannot repair itself from inside the harness.

This is a prerequisite boundary, not a hidden install step. Do not add shell, batch, PowerShell, MCP, IDE trust, or machine-local settings to work around it.

## First Check

Run whichever commands are available in the user's terminal:

```shell
python --version
py -3.12 --version
python3 --version
```

If one reports Python 3.12 or newer, use that executable explicitly:

```shell
python -B .agents/manage.py setup --check
py -3.12 -B .agents/manage.py setup --check
python3 -B .agents/manage.py setup --check
```

## No Admin Rights

Use a user-writable Python runtime. The harness does not require Python packages for its core setup, routing, workflow, and validation scripts; those scripts use the Python standard library.

On Windows, acceptable no-admin options are:

- A user-scoped Python install from the official Python installer when organization policy allows it.
- A user-scoped WinGet install when `winget` is available and organization policy allows package-manager installs.
- The official Windows embeddable Python package extracted into a user-writable folder such as `%LOCALAPPDATA%\Programs\Python\Python312` or an ignored repo-local cache folder.
- An internally approved portable Python runtime supplied by IT.

WinGet can request current-user scope. Run it only after the user approves installing software:

```shell
winget search -e --id Python.Python.3.12
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
```

If the package or organization policy rejects user scope, use an approved portable runtime instead. Open a new terminal after installation before relying on `PATH`.

Run with the full executable path when Python is not on `PATH`:

```shell
C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe -B .agents/manage.py setup --check
```

On Linux or macOS, use an existing `python3` when it is 3.12+, or use an organization-approved user-level/portable Python runtime. If the machine cannot install developer tools and has no approved runtime, setup is blocked until one is provided.

## Stable Runtime Lookup

Do not add repo-local shell aliases or wrapper scripts. Aliases are shell-specific, are often invisible to noninteractive agent commands, and make several project folders drift.

Use this lookup order for the first harness command:

1. `AGENTS_PYTHON` when it points to a Python 3.12+ executable.
2. The repo-local portable cache path for the current OS and architecture.
3. A normal executable on `PATH` such as `python`, `py -3.12`, or `python3`.
4. Stop and ask for an approved runtime when none exists.

After `.agents/manage.py` starts, harness-owned child Python commands use the current interpreter, so subprocesses stay on the same runtime.

For one project, a repo-local ignored cache is fine. For several projects, prefer one shared user-level runtime and point every project at it with `AGENTS_PYTHON`.

Windows example:

```shell
[Environment]::SetEnvironmentVariable("AGENTS_PYTHON", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "User")
& $env:AGENTS_PYTHON -B .agents/manage.py setup --check
```

Linux/macOS example:

```shell
export AGENTS_PYTHON="$HOME/.local/share/ai-harness/python/bin/python3"
"$AGENTS_PYTHON" -B .agents/manage.py setup --check
```

## Portable Runtime Cache

Do not commit Python executables into the project. Instead, place an approved portable runtime under the ignored repo-local tool cache, or under another user-writable folder, and run it by full path.

Suggested cache layout:

```text
.agents/tools/cache/python/windows-x64/python.exe
.agents/tools/cache/python/linux-x64/bin/python3
.agents/tools/cache/python/macos-arm64/bin/python3
.agents/tools/cache/python/macos-x64/bin/python3
```

Example commands:

```shell
.\.agents\tools\cache\python\windows-x64\python.exe -B .agents/manage.py setup --check
./.agents/tools/cache/python/linux-x64/bin/python3 -B .agents/manage.py setup --check
./.agents/tools/cache/python/macos-arm64/bin/python3 -B .agents/manage.py setup --check
```

The cache folder is ignored by Git. Track only manifests, docs, and validation policy, not runtime binaries.

## Why Not Commit Python Binaries

Do not add portable Python executables directly to this repository:

- They are large and multiply across Windows, macOS, Linux, x64, and ARM64.
- They create supply-chain, code-signing, antivirus, and quarantine concerns.
- They require license and notice tracking for Python and bundled native libraries.
- They carry security-update and CVE patching responsibility.
- Corporate machines may still block unsigned or unapproved binaries even when they are portable.

The maintainable pattern is a small tracked policy plus an ignored, reproducible, user-approved runtime cache.

## Restricted Network

If the user has no admin rights and no network access, do not ask the harness to download anything. Request an internally approved Python 3.12+ runtime archive through the team's normal software distribution path, extract it to a user-writable folder, then run setup with the full interpreter path.

## Codex Desktop

Inside Codex Desktop, an agent may have access to a bundled Python runtime for the current task. That helps the agent work in the shared workspace, but it does not make the target project independently runnable for users in their own terminal. A consumer project still needs a local Python 3.12+ runtime for `setup`, workflows, and validation.

## What Not To Do

- Do not add active PowerShell, batch, shell, or command-wrapper bootstrap files.
- Do not commit Python runtimes, installers, caches, or extracted binaries.
- Do not silently install Python globally.
- Do not claim setup is ready when no Python 3.12+ runtime is available.

## After Python Exists

From the project root, run:

```shell
python -B .agents/manage.py setup
python -B .agents/manage.py setup --check
python -B .agents/manage.py status --fast
```

If a specific executable path is required, replace `python` with that path in all commands.
