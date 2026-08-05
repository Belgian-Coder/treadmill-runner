---
name: playwright-integration
description: Use when checking, preparing, or reporting Playwright browser-test readiness for a local project without making browser setup part of another quality-gate skill.
---

# Playwright Integration

## Goal

Provide deterministic Playwright readiness and screenshot validation that workflows and quality gates can reuse for Node.js, Python, and .NET projects without hiding installs, browser downloads, or project writes.

## Workflow

1. For strict no-temp/no-write/no-profile offline dogfood, use docs, help, and `module.json.strict_read_only_commands` only. Exclude report paths, setup, capture, probes, servers, installs, local AI, and workflow writes. A helper may exit non-zero for useful skipped/blocked evidence; classify its status before treating it as failed.
2. Inspect the narrow app/project root for Node.js `package.json`, Python `requirements*.txt`/`pyproject.toml`, .NET project files, framework, Playwright dependencies, Playwright scripts, config files, existing specs, CI files, reporters, and ignored output folders. Avoid broad repository-root scans on large multi-project repos unless the repo root is the actual Playwright project.
3. The detector recognizes .NET `Microsoft.Playwright*` PackageReferences and Python `playwright`/`pytest-playwright` declarations without requiring Node.js.
4. Run the readiness script in report-only mode first:

```shell
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root> --output-json <workflow-validation-folder>/playwright-readiness.json
```

5. With workflow/user approval, `--auto-install` implies `--auto-configure`, plans language-specific package/browser setup, and writes minimal artifact ignores. Use `--preflight-install` first; report paths make this workflow evidence, not strict dogfood:

```shell
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root> --auto-install --preflight-install
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root> --auto-install --output-json <workflow-validation-folder>/playwright-readiness.json
```

6. For visual validation, start the app with a workflow-owned command, then capture desktop/mobile screenshots into the workflow validation folder. Pass `--accepted-dir` only for committed end-state screenshots in a separate project-owned folder.

```shell
python -B .agents/skills/playwright-integration/scripts/validate_playwright_screenshots.py --project-root <project-root> --url <local-url> --validation-dir <workflow-validation-folder>/playwright --skip-llm-analysis
python -B .agents/skills/playwright-integration/scripts/validate_playwright_screenshots.py --project-root <project-root> --url <local-url> --validation-dir <workflow-validation-folder>/playwright --accepted-dir <project-root>/validation/playwright/accepted
python -B .agents/manage.py workflow validation-packet --name <workflow-name> --run-id <run-id> --kind playwright-screenshots --format json
```

Default viewports are desktop `1440x900` and mobile `390x844`; override only when declared. Backend `auto` prefers project-local Node.js, then `npx --no-install`, then Python Playwright. Skip LLM analysis unless policy/workflow permits local vision. Missing Playwright is a skipped capability, not a .NET quality failure.
7. For project shape, lint, result parsing, and flaky diagnosis, use deterministic helper scripts before any local-AI summary:

```shell
python -B .agents/skills/playwright-integration/scripts/detect_playwright_project.py --project-root <project-root> --report-json <workflow-validation-folder>/playwright-detect.json
python -B .agents/skills/playwright-integration/scripts/lint_playwright_tests.py --project-root <project-root> --changed-files --report-json <workflow-validation-folder>/playwright-lint.json
python -B .agents/skills/playwright-integration/scripts/parse_playwright_results.py --results-json <results.json> --report-json <workflow-validation-folder>/playwright-results-summary.json
python -B .agents/skills/playwright-integration/scripts/diagnose_flaky_playwright.py --input <failure-output.txt> --report-json <workflow-validation-folder>/playwright-flaky.json
```

Local AI may summarize the readiness JSON only when metadata and `.agents/local-ai/policy.json` allow it:

```shell
python -B .agents/manage.py local-ai task --task validation-triage --input <evidence-folder>/playwright-readiness.json
```

Fallback without local AI: read `status`, `checks`, `skipped`, `blocked`, and `commands` from the JSON report.

## Rules

- Do not install packages or browsers unless `--install`, `--install-browsers`, or `--auto-install` is explicitly passed after workflow/user approval.
- Use `--preflight-install` to show install commands without running them, and `--probe-runtime` only when local process execution is allowed for no-install Playwright metadata probes.
- Strict read-only/offline excludes report paths, setup/install/server flags, screenshots, local AI, workflow writes, and temp-fixture self-tests. `--auto-install --preflight-install` is approved planning only.
- Report missing Node.js, npm, Python, .NET SDK, package files, config, or Playwright declarations as evidence; do not guess.
- Runtime PATH checks are blockers only for the matching declared language support or when setup/runtime probing was requested; do not overstate missing unrelated runtimes.
- Treat .NET Playwright browser-install commands as generated project setup after `dotnet build`; use project-documented commands when target frameworks or scripts cannot be inferred.
- Screenshot validation requires a running URL owned by another workflow step. Save evidence under workflow validation; save committed end-state images only with `--accepted-dir`. If vision is unavailable, retain the prompt and report analysis skipped/failed.
- Prefer role/label/text/placeholder/test-id locators, web-first assertions, baseURL-relative navigation, independent tests, and explicit file-attachment/form-submit approval.
- Report skipped, failed, and blocked setup as non-blocking when the selected workflow can still continue with metadata-only evidence.
- Do not commit generated browser binaries, node_modules, reports, or environment-specific settings.
- Treat browser setup as optional unless the selected workflow made it required.
- Keep BrowserStack, TestRail, Slack, MCP servers, Claude hooks, and always-on validation hooks out of this skill unless a future explicit plan adds them with credential boundaries.

Detailed candidate-ingestion decisions, quality rules, and evidence examples live in `docs/playwright-quality-guide.md` and `docs/playwright-evidence-examples.md`.

## Validation

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/playwright-integration
python -B .agents/skills/playwright-integration/scripts/run_self_tests.py
```

Self-tests and evals are implementation validation; run them only when temp fixtures and generated artifacts are allowed. In strict dogfood, skip them and report the stdout-only helper evidence instead.

## Stop Rules

- Stop before running installs without explicit workflow/user approval.
- Stop before setup, test-run, or screenshot claims if `--project-root` lacks readable Node.js, Python, or .NET Playwright declarations; detection/readiness may still report the absence as skipped metadata evidence.
- Stop before claiming browser tests can run when readiness only checked metadata.
- Stop before claiming visual validation passed unless fresh desktop and mobile screenshots were captured and saved in the workflow validation folder.
- Stop before submitting forms or attaching files unless the selected workflow/user explicitly approved that real-world action.

## Completion Contract

Report project root, package/config findings, language support findings, lint findings, install flags, commands run, output JSON paths, screenshot paths, LLM analysis path/status, readiness status, skipped prerequisites, blocked prerequisites, failed commands, validation result, and remaining browser-test risk.

Report `Skill used: playwright-integration - <reason>` when this skill materially affected the work.
