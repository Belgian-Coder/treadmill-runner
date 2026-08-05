# Playwright Quality Guide

Reusable Playwright ideas from reviewed temporary candidates. The accepted implementation is repo-owned Python and Markdown; no candidate shell hooks, tool settings, slash commands, subagents, or remote service integrations are imported.

## Candidate Decisions

| Source | Taken | Rejected |
|---|---|---|
| `playwright-automation-fill-in-form` | Never submit forms without review; record fields, attachments, submit state. | Hardcoded Microsoft Forms target, fixed data, MCP-only flow, real submission. |
| `playwright-explore-website` | Explore 3-5 core flows; record interactions, locators, expected outcomes. | Browser MCP dependency, automatic test generation side effect. |
| `playwright-generate-test` | Explore first, execute scenario steps, then run generated tests with evidence. | Unbounded writes to `tests/`, no repo guardrails. |
| `playwright-pro` | Locator priority, web-first assertions, anti-patterns, flaky taxonomy, coverage matrix, result shape, migration and framework/config detection. | Claude hooks, tool settings, slash commands, subagents, TestRail/BrowserStack MCP, Slack/TestRail pushes, large template forest, always-on hooks. |
| `dotnet-artisan/dotnet-engineering/playwright.md` | Detect `.csproj` files with `Microsoft.Playwright*` PackageReferences. | Package installs, browser downloads, trace upload/viewing flows, generated .NET templates. |

`playwright-pro` is MIT licensed. The accepted skill rewrites concepts into repo-owned Python; preserve attribution if future work copies text or code directly.

## Strict Stdout-Only Commands

Use these for no-temp/no-write dogfood when the inputs already exist. Do not add report paths, setup, capture, runtime-probe, local-AI, or workflow flags.

```shell
python -B .agents/skills/playwright-integration/scripts/detect_playwright_project.py --project-root <project-root>
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root>
python -B .agents/skills/playwright-integration/scripts/lint_playwright_tests.py --project-root <project-root> --changed-files
python -B .agents/skills/playwright-integration/scripts/parse_playwright_results.py --results-json <existing-results.json>
python -B .agents/skills/playwright-integration/scripts/diagnose_flaky_playwright.py --input <existing-output.txt>
```

These commands may exit non-zero when the printed report status is `skipped`, `blocked`, or `failed`. Treat that as evidence first; read the report status before deciding whether the command itself failed unexpectedly.

## Workflow Evidence Commands, Not Strict Read-Only

```shell
python -B .agents/skills/playwright-integration/scripts/detect_playwright_project.py --project-root <project-root> --report-json <evidence>/playwright-detect.json --report-md <evidence>/playwright-detect.md
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root> --auto-install --preflight-install
python -B .agents/skills/playwright-integration/scripts/check_playwright_readiness.py --project-root <project-root> --auto-install --preflight-install --output-json <evidence>/playwright-readiness.json
python -B .agents/skills/playwright-integration/scripts/validate_playwright_screenshots.py --project-root <project-root> --url <local-url> --validation-dir <evidence>/playwright --skip-llm-analysis
python -B .agents/skills/playwright-integration/scripts/lint_playwright_tests.py --project-root <project-root> --changed-files --report-json <evidence>/playwright-lint.json --report-md <evidence>/playwright-lint.md
python -B .agents/skills/playwright-integration/scripts/parse_playwright_results.py --results-json <project-root>/playwright-results.json --report-json <evidence>/playwright-results-summary.json
python -B .agents/skills/playwright-integration/scripts/diagnose_flaky_playwright.py --input <evidence>/playwright-failure.txt --report-json <evidence>/playwright-flaky.json
```

Use local AI only to summarize these reports when `.agents/local-ai/policy.json` allows it. Fallback: read JSON fields directly.

Detection/readiness reports include Node.js, Python, and `.NET` Playwright signals. Python signals use `requirements:<path>:<package>` or `pyproject:<path>:<section>:<package>`. `.NET` signals use `csproj:<path>:<package>`.

`--auto-install` is the approved setup path for explicit workflow/user requests. It plans Node.js npm setup, Python pip/browser setup, and .NET build/browser-script setup from declarations; `--preflight-install` records the commands without running them. The stdout-only preflight is approved setup planning evidence; adding `--output-json` makes it workflow evidence, not strict dogfood. Screenshot validation captures desktop `1440x900` and mobile `390x844` images by default and writes them below the workflow validation folder. Pass `--skip-llm-analysis` unless local-AI vision is allowed by policy/workflow; otherwise it also writes local-AI vision analysis when available.

Screenshot backend `auto` first uses a project-local Node.js Playwright package, then `npx --no-install playwright screenshot`, then Python Playwright. This keeps Node.js, Python, and .NET web apps compatible as targets; .NET apps are validated by running the app URL and capturing it through an available Playwright browser backend.

Readiness reports always include Node.js, npm, Python, and .NET PATH facts for transparency. Treat a missing runtime as a blocker only when the matching language support is declared or when setup/runtime probing requested that runtime.

## Test Quality Rules

- Prefer locators in order: `getByRole`, `getByLabel`, `getByText`, `getByPlaceholder`, `getByTestId`, then CSS/XPath only as last resort.
- Prefer web-first assertions such as `await expect(locator).toBeVisible()` over one-shot values like `expect(await locator.textContent())`.
- Do not use `page.waitForTimeout()` for readiness; wait for a locator, event, response, or assertion.
- Do not use `test.only`, serial tests, step-number test names, or shared mutable top-level state.
- Keep `baseURL` in config and use `page.goto('/path')` where possible.
- Record file attachments and stop before real submission unless the workflow/user approves.

## Flaky Diagnosis

Classify before patching:

| Category | Evidence | First Checks |
|---|---|---|
| timing-async | timeout, hidden/detached element, missing await | repeat locally, trace once, replace arbitrary waits |
| test-isolation | passes alone, fails in suite, duplicate data | run one worker, isolate storage/data |
| environment | CI-only, browser-specific, timezone/font/viewport | compare CI and local runtime facts |
| infrastructure | crash, OOM, DNS/network reset | reduce workers, check dependencies/logs |

## Workflow Evidence Packet

For user-story and bug workflows, attach:

- readiness JSON from `check_playwright_readiness.py`;
- detection JSON from `detect_playwright_project.py`;
- screenshot validation JSON, desktop/mobile screenshots, and `llm-analysis.md` or skipped analysis details from `validate_playwright_screenshots.py`;
- lint JSON/Markdown for changed or relevant specs;
- result summary JSON when tests ran;
- flaky diagnosis JSON for intermittent failures.

Do not claim browser tests are runnable unless readiness checked executable/runtime facts or a real test command produced fresh output. Do not claim visual validation passed unless desktop and mobile screenshots were freshly captured and attached.
