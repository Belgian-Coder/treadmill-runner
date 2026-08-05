# Playwright Evidence Examples

## User Story Workflow

```json
{
  "tool": "playwright-integration",
  "story": "user can save profile settings",
  "readiness_report": "runs/<run-id>/evidence/playwright-readiness.json",
  "project_detection": "runs/<run-id>/evidence/playwright-detect.json",
  "screenshot_validation": "runs/<run-id>/validation/playwright/playwright-screenshot-validation.json",
  "screenshots": [
    "runs/<run-id>/validation/playwright/screenshots/desktop-1440x900.png",
    "runs/<run-id>/validation/playwright/screenshots/mobile-390x844.png"
  ],
  "llm_analysis": "runs/<run-id>/validation/playwright/llm-analysis.md",
  "lint_report": "runs/<run-id>/evidence/playwright-lint.json",
  "result_summary": "runs/<run-id>/evidence/playwright-results-summary.json",
  "claim": "Browser coverage exists only for behaviors named in result_summary.tests",
  "local_ai_fallback": "Read JSON summary and findings directly when local AI is disabled."
}
```

## Bug Ticket Workflow

```json
{
  "tool": "playwright-integration",
  "bug": "checkout test fails intermittently in CI",
  "failure_output": "runs/<run-id>/evidence/playwright-failure.txt",
  "flaky_diagnosis": "runs/<run-id>/evidence/playwright-flaky.json",
  "lint_report": "runs/<run-id>/evidence/playwright-lint.json",
  "reproduction": "Fresh command output is required before claiming reproduction or non-reproduction.",
  "local_ai_fallback": "Use deterministic category, recommended_commands, and lint findings directly."
}
```

## Local AI Advisory Snippet

```shell
python -B .agents/manage.py local-ai task --task validation-triage --input <evidence>/playwright-lint.json
```

Fallback without local AI: report `status`, `summary`, `findings`, `recommended_commands`, `skipped`, and `blocked` from the JSON reports.

## Screenshot Validation

```json
{
  "tool": "playwright-integration.screenshot-validation",
  "validation_dir": "runs/<run-id>/validation/playwright",
  "default_viewports": [
    {"name": "desktop", "width": 1440, "height": 900},
    {"name": "mobile", "width": 390, "height": 844}
  ],
  "prompt_packet": "runs/<run-id>/validation/playwright/llm-analysis-prompt.md",
  "local_ai_fallback": "If local AI vision is unavailable, keep the prompt packet and screenshot paths, and report llm_analysis.status as skipped or failed."
}
```
