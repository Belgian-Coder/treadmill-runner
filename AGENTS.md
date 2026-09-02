# Repo Instructions

## Paths

- Skills live under `.agents/skills/`; workflows and run evidence live under `automations/<name>/`.
- Read `docs/project/agent-rules.md` when a consumer repository provides it.
- Do not edit generated files: routing, registries, instruction adapters.

## Rules

- V1 only; remove obsolete forms after breaking improvements.
- Remote Start needs explicit approval; never replay it.
- Use `$skill-manager` for skills, `$workflow-manager` for workflows, `$mermaid-diagrams-azure-devops` for Azure DevOps Mermaid.
- Route first: `.agents/routing.md`, `automations/routing.md`, selected `module.json`/entry file, and `automations/navigation/artifacts/maps/HANDOFF.md` when present; raw navigation JSON is tool-only.
- Workflow self-discovery: for workflow-shaped work, run `python -B .agents/manage.py workflow start --from-request "<request>" --summary --compact --format json`. For ambiguous/read-only/no-start, run `python -B .agents/manage.py which-workflow "<request>" --summary --compact --format json`; report owner/confidence/next command if read-only/no-start, else follow `next_command`.
- Workflow lifecycle owns retrieval/evidence: use compact `workflow start`/`resume`, `workflow finish`; don't ask users to run internal local-AI commands. Resume/handoff: infer from `automations/*/runs/<run-id>/run.json`, run `python -B .agents/manage.py workflow context-audit --name <workflow-name> --run-id <run-id> --summary --compact --format json`, follow `next_command` unless read-only. Changed files: `status --no-local-ai --summary --compact --format json` or `changed-evidence --summary --compact --format json`.
- Ticket runs: `US-<id>` for stories, `BUG-<id>` for bugs; dates stay in run files.
- Automatic search policy: use `rg` for exact facts. For structural code shapes, use optional ast-grep silently when `rg` over-selects; emit `file:line:snippet`, never load raw ast-grep JSON or navigation-map JSON, and fall back to `rg` or Python `ast`. See `docs/reference/tools-and-search.md`.
- Daily path: `docs/operations/daily-agent-path.md`; larger changes: `automations/disciplined-change-workflow/WORKFLOW.md`.
- Skip generated `registry.json` for normal routing.
- Prefer Markdown for human files.
- Docs `docs/**/*.md`: require frontmatter `title`, `type`, `status`, `owner`, `audience`, `updated`; link from start/map.
- Do not commit dogfood/temp evidence by default; promote durable lessons into docs, suites, scripts, templates, fixtures, or reports.
- Keep behavior in its owning skill/workflow folder.
- Harness tools use Python 3.12+ stdlib. Consumer repos may use project-approved deterministic platform scripts.
- Do not commit local tool/trust/personal settings.
- Bounded governor: for clear, reversible, low-consequence work with one verifier, stay serial, use `implementation-low`, implement once, verify once, then stop; escalate otherwise. Do not delegate or repeat unchanged work without new evidence.
- Orchestration: read `orchestration.md` and run `workflow route-model`; routes are preferences and never force a subagent.
- Serialize build/test commands sharing `bin/`, `obj/`, coverage, browser, or generated outputs unless isolated or leased.
- Validation is two-stage. During implementation, run only `eng/verify-change.ps1 -TestFilter '<affected tests>'` and add `-BrowserFilter '<affected browser tests>'` only when UI/browser behavior changed. The wrapper refreshes stale browser output, otherwise reuses it. Do not run the full suite after each edit. Once implementation and focused checks are green, commit the reviewed change, then run `eng/verify-change.ps1 -Full` exactly once for final acceptance; use `-NoBrowser` only when the diff since the latest release contains no Web, E2E, Playwright, shared build, or browser asset file. Rerun it only after repairing a failure from that gate. A clean full gate records a commit-bound receipt that the release script may reuse for eight hours, preventing duplicate release validation. Focused .NET/browser phases cap at one/two minutes; complete .NET/browser phases cap at two/five minutes. Both stream durable progress logs and stop after 60/90 seconds without output.
- Connect IQ is excluded from normal validation. Add `-IncludeConnectIq` to the final `eng/verify-change.ps1 -Full` command only when changes touch `connectiq/**`, the companion build/validation scripts, or companion-specific contracts/resources.
- Announce skill use when material: `Using <skill> for <concrete reason>`; not `through`.

## Completion Contract

Before finalizing, report low-context files used/skipped, changed paths, commands, generated artifacts, validation, skipped/blocked/failed checks, risks, material skills, and `Skill used: <name> - <reason>` when material. Use `Skipped: <check> - <reason>`; summarize blocked/failed commands.

## Commands

First-time: `docs/start-here.md`; consumers: `install-harness`. Run `python -B .agents/manage.py setup --check` first time; normal: `next-action`, `status --fast`, `sync`, `python -B .agents/manage.py finish`; deep: `python -B .agents/manage.py finish --deep` for impacted integration checks; exhaustive release: `python -B .agents/manage.py finish --release-full`.

## Release Policy

- GitHub Actions is disabled in repository settings, and the repository contains neither workflows nor Dependabot update configuration. Commits, pull requests, and tags must never start hosted builds; all validation, building, signing, and packaging runs on the release workstation.
- Never create, move, or push a release tag by hand. From a clean `main` that exactly matches `origin/main`, use `eng/create-github-release.ps1 -Version <MAJOR.MINOR.PATCH> -ReleaseNotes '<notes>'`.
- Signing stays on the release workstation with the non-exportable certificate. Never place its private key, a PFX, or a signing password in GitHub secrets or repository files.
- The script owns local validation, signed packaging, annotated tag creation, draft asset verification, and publication. It uploads the locally produced assets directly to GitHub Releases without a hosted workflow. If interrupted, rerun the exact version and exact release notes; it may resume only the matching tag and draft and never force-moves a tag.
- Do not create a tag or release during unrelated work or without an explicit version/release request. The canonical procedure and recovery rules are in `docs/project/release-operations.md`.

<!-- global-model-routing:start -->
## Model and subagent routing

These instructions explicitly request delegation and therefore authorize subagent use
under Codex's default multi-agent runtime policy.

- Keep normal work in the root agent on the user's selected model. The normal
  operating point is GPT-5.6-Sol Medium on Standard service tier.
- The user's explicitly selected model, reasoning effort, and service tier are
  authoritative. Never downgrade them. Never select Fast/Priority automatically,
  never request a child service-tier override, and never use GPT-5.6-Terra.
- Only these automatic routes are allowed:
  - GPT-5.6-Sol Medium for normal engineering, debugging, integration, UI/UX,
    accessibility, review, final synthesis, and verification.
  - GPT-5.6-Luna Medium for substantial independent read-heavy work such as
    repository mapping, inventories, large searches, log analysis, bulk comparison,
    evidence gathering, classification, or failure categorization.
  - GPT-5.6-Luna XHigh for substantial self-contained implementation with clear
    ownership and objective acceptance criteria.
  - GPT-5.6-Sol High for difficult judgment, ambiguity, architecture, security,
    concurrency, subtle correctness, hard review, or exact visual alignment.
- Before substantial work, evaluate delegation. Proactively spawn an appropriate
  subagent without waiting for the user when at least one condition applies:
  - a substantial bounded workload can be materially cheaper on Luna;
  - a large read-heavy investigation can run independently;
  - implementation has a clean ownership boundary;
  - substantial independent workloads can run concurrently;
  - repetitive work across many files would consume significant root-agent work;
  - independent specialist analysis justifies another context.
- Do not delegate small tasks, simple searches, routine tests, or minor edits. Do
  not spawn another Sol Medium child for work the root can reasonably perform.
- Prefer one coherent child that inspects, implements, tests, corrects, and reports.
  Prefer root plus one useful child over several tiny children. Use multiple children
  only for substantial independent workloads.
- Give write-capable children non-overlapping ownership. Tell them they are not
  alone in the codebase, must preserve unrelated and concurrent changes, and must
  adapt to edits made by others.
- Minimize context duplication. Prefer self-contained assignments with
  `fork_turns = "none"`; otherwise use the smallest useful recent-turn count.
  Reuse an existing child when follow-up work depends on its accumulated context.
- Do not use automatic escalation ladders. Diagnose environment, context, ownership,
  and instruction failures before changing model or reasoning level.
- Do not automatically spawn review agents. Prefer compiler/build output, tests,
  linters, browser checks, screenshots, and existing validation tools. Use an
  independent Sol High review only when complexity or risk justifies it.
<!-- global-model-routing:end -->
