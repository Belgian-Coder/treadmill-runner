# SonarQube Diagnostics Evidence Examples

## Credential Boundary

- Use `SONAR_TOKEN` or an explicit runtime token; do not commit scanner credentials, tokens, or credentialed URLs.
- Read-only exports assert `no_upload_assertion: true`.
- Scanner publishing remains explicit and separate from diagnostics exports.

## Deterministic Packets

These examples require network access and write evidence files; do not run them in strict offline/no-write dogfood.

```shell
python -B .agents/skills/sonarqube-diagnostics/scripts/export_issues.py --base-url https://sonar.example --project-key Project --output-json validation/sonar-issues.json --output-md validation/sonar-issues.md --output-sarif validation/sonar-issues.sarif
```

```shell
python -B .agents/skills/sonarqube-diagnostics/scripts/export_coverage.py --base-url https://sonar.example --project-key Project --output-json validation/sonar-coverage.json --output-md validation/sonar-coverage.md
```

Expected evidence includes normalized issue severity/category, redacted URLs, read-only status, quality/coverage summaries, SARIF-compatible export when requested, skipped or failed credential checks, and retry/rate-limit errors when the server blocks access.
