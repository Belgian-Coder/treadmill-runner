---
name: sonarqube-diagnostics
description: Use when exporting SonarQube diagnostics, comparing local and remote coverage, exporting quality profiles, or explicitly publishing scanner analysis with clear credential and upload boundaries.
---

# SonarQube Diagnostics

## Goal

Make SonarQube usage explicit and auditable. Diagnostic exports are read-only by default; scanner publishing must be requested deliberately.

## Workflow

1. For strict read-only/offline/no-profile/no-temp/no-write dogfood, use docs, script `--help`, and source-reviewed stdout validators only:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/sonarqube-diagnostics --summary --compact --format json
python -B .agents/manage.py inspect-skill --skill .agents/skills/sonarqube-diagnostics --fast --summary --compact --format json
python -B .agents/manage.py measure-skill-budget --skill sonarqube-diagnostics --summary --compact --format json
python -B .agents/skills/sonarqube-diagnostics/scripts/export_issues.py --help
python -B .agents/skills/sonarqube-diagnostics/scripts/compare_coverage.py --help
```

Skip `credential-doctor`, including summary mode, when profile reads are disallowed. Re-review manager commands if changed. Do not call SonarQube, pass live URLs/tokens, write outputs, run local AI, self-tests, or scanner commands.
2. Use no-upload export scripts when network and workflow evidence writes are allowed: issues, hotspots, coverage measures, and quality profile metadata. Here, read-only export means no SonarQube upload, not offline/no-write; it still performs network reads and usually writes evidence.
3. Outside strict dogfood, before any live SonarQube call, check local service readiness:

```shell
python -B .agents/manage.py credential-doctor --summary --compact --format json
```

If no profile exists, collect profile name, base URL, token source, and project key when needed. Quality-profile exports may use `--language` or `--quality-profile`. Save profile details with `credential-doctor --configure` to gitignored `.agents/local-ai/secrets.local.json`; prefer `SONAR_TOKEN`, and store tokens only with explicit consent.

```shell
python -B .agents/manage.py credential-doctor --configure --service sonarqube --name project-a --base-url https://sonar.example --project-key ProjectA --token-env SONAR_TOKEN --format json
```

4. Store exports under the workflow work folder, usually `validation/sonarqube/`.
5. Compare local coverage evidence with SonarQube coverage using `compare_coverage.py` before treating remote data as authoritative. Stdout-only compare means existing approved JSON and no `--output-json`.
6. Use `run_analysis.py --publish` only when the workflow explicitly needs scanner publishing; without `--publish` it emits a no-upload skipped packet. Even without `--publish`, `run_analysis.py` is not strict-dogfood safe because it is scanner/runtime oriented.
7. Consume JSON exports through `schema_version`, `tool`, `ok`, `status`, `read_only`, `no_upload_assertion`, `summary`, `checks`, `skipped`, and normalized severity/category rows.

```shell
python -B .agents/skills/sonarqube-diagnostics/scripts/export_issues.py --base-url https://sonar.example --project-key ProjectKey --output-json validation/sonarqube/issues.json --output-md validation/sonarqube/issues.md
```

Local AI may triage already-exported JSON only; fallback is reading `summary`, `normalized_issues`, and `checks`.

## Rules

- Do not publish scanner analysis unless the user or workflow explicitly requests it.
- Strict read-only/offline/no-profile/no-temp/no-write excludes live export scripts, `--base-url`, `--server-name`, tokens, output path flags, all credential/profile commands, local-AI task calls, `run_analysis.py`, `--publish`, and self-tests that create temp fixtures.
- Read-only export payloads must include `no_upload_assertion: true`. Scanner publishing requires `run_analysis.py --publish`.
- Do not print tokens or write them to output files.
- Outside strict dogfood, when config is missing, ask for required service details and run `credential-doctor --configure`; never guess URLs, project keys, or token sources.
- Keep SonarQube exports read-only unless running `run_analysis.py`.
- Record project key, base URL, export timestamp, and endpoint status.
- Treat quality profile export as diagnostic evidence, not as project configuration.
- Do not use side or sub agents to call SonarQube. Use them only to interpret exported evidence after the scripts run.

## Validation

Strict no-write validation:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/sonarqube-diagnostics --summary --compact --format json
python -B .agents/manage.py inspect-skill --skill .agents/skills/sonarqube-diagnostics --fast --summary --compact --format json
python -B .agents/manage.py measure-skill-budget --skill sonarqube-diagnostics --summary --compact --format json
```

Temp-write validation:

```shell
python -B .agents/skills/sonarqube-diagnostics/scripts/run_self_tests.py
```

## Stop Rules

- Stop if publishing or project-scoped export lacks an explicit project key, SonarQube URL, or token source.
- Stop before writing raw responses that may include secrets.
- Stop before claiming scanner results were published if `run_analysis.py` did not complete successfully.

## Completion Contract

Report endpoints queried, output files, coverage comparison result, scanner publish status, validation status, skipped exports, blocked credentials or endpoints, failed command summaries, and remaining diagnostic risk.

Report `Skill used: sonarqube-diagnostics - <reason>` when this skill materially affected the work.
