# Automation Workflows

Automation modules coordinate phases, scripts, templates, hooks, workflow-owned outputs, and evidence packets under `automations/`; skills stay reusable.

Use `automations/routing.md` first. Create/import only when no owner covers the trigger and behavior exceeds one command, static policy, copied skill text, or reusable helper.

## Intake

Before changes, confirm owner/new-vs-extend, related skills referenced not copied, script inputs/outputs/purpose, owned or declared outputs, resume/evidence/validation/hooks/budgets, and `check-additions` ownership.

## Layout

Required: `WORKFLOW.md` and `module.json`.

Optional when justified: `instructions.md`, `diagrams/`, `scripts/`, `templates/`, `suites/`, `runs/`, `docs/`, `assets/`, `artifacts/`.

`WORKFLOW.md` is human entry; `module.json` is the machine contract. Use Markdown for human files unless JSON improves validation or interop.

## module.json

Keep machine facts in `module.json`:

```text
schema_version, kind, id, version, summary, owners, risk, inputs, outputs,
commands, related_modules, validation, external_access, local_ai, context_evidence,
phases, tasks, worker_profiles, hooks
```

Reuse them in routing, validators, adapters, evidence, run packets. Phase state belongs in `run.json`.

## Tasks And Workers

Use `tasks` when phases are too coarse. Task IDs are lowercase hyphen-case; dependencies stay inside one workflow; cycles fail validation. Runtime status, blockers, decisions, evidence stay in `run.json`.

Use `module.json.worker_profiles` for phase execution guidance. Extend `portable-default`, assign every phase, keep `max_parallel_workers` low, and never require spawning. Each semantic profile carries a route-set ID, prompt adapter, context budget, tool policy, expected output, and validation gate. Runtime attestation separately resolves a host-surface route, model overlay, and surface adapter; context packets and handoffs preserve those decisions. Local AI is advisory; command exits and evidence stay authoritative.

Keep behavior on three axes: the workflow assigns a stable semantic profile; trusted observed model-provider identity selects a bounded prompt overlay; trusted host-surface capabilities select available orchestration, continuation, caching, instruction surfaces, and usage handling. Effective native orchestration additionally requires the phase safety policy, task class, provider-backed economics gate, and explicit delegation request. Normative instructions and templates name profile IDs. Run evidence records the profile, observed host, model provider/model, deliberation evidence, capabilities, overlay, surface adapter, available/effective orchestration, and every fallback blocker. Neither overlays nor surface adapters can change tools, authority, output, delegation, or validation. See [Model Compatibility And Routing](../../../../docs/reference/model-compatibility-and-routing.md).

Inspect workers with:

```shell
python -B .agents/manage.py workflow workers --all --summary --compact --format json
python -B .agents/manage.py workflow workers --profiles --format json
python -B .agents/manage.py workflow workers --name <workflow-name> --phase <phase-id>
python -B .agents/manage.py workflow workers --name <workflow-name> --phase <phase-id> --run-id <run-id> --delegation-requested
```

## Hooks And Context Evidence

Hooks are deterministic lifecycle work for state-writing commands, e.g. evidence generation or cache refresh. Hook commands use Python stdlib entrypoints via `.agents/manage.py`, `.agents/skills/...`, or `automations/<workflow>/scripts/...`; output stays under the run folder.

Every workflow declares `module.json.context_evidence.required: true` plus start/resume/finish queries. Lifecycle commands write `validation/context-evidence-<event>.json` and `.md`. Path-bounded fallback is acceptable; block only when neither source proves context.

Inspect hooks with:

```shell
python -B .agents/manage.py workflow hooks --name <workflow-name> --format json
python -B .agents/manage.py workflow hooks --all --check --format json
```

## Run Evidence

New runs use only:

```text
runs/<run-id>/run.json
runs/<run-id>/REPORT.md
runs/<run-id>/validation/
runs/<run-id>/artifacts/
```

`run.json` records phase, status, decisions, skipped/blocked/failed checks, commands, evidence paths, handoff, unsupported claims, external validation, next action. Move durable lessons to docs, suites, scripts, templates, fixtures, or compact reports; do not retain dogfood runs unless requested.

Generate indexes for committed packets:

```shell
python -B .agents/manage.py index-workflow-runs --name <workflow-name> --write
```

## Instructions And Diagrams

Use `instructions.md` only when phases need detail. Each step names source, action, target, done condition, blocked behavior, evidence path, parallel safety.

Every workflow needs Azure-compatible Mermaid diagrams:

- process view: phases, decisions, stop rules, handoff;
- connection view: systems, skills, workflows, services, evidence stores;
- low-level view: multi-module, service, job, API, queue, generated-file, or artifact interactions;
- `erDiagram`: schema, persistence, migration, table ownership, or data relationships.

If skipped, record the reason in `run.json` and `REPORT.md`.

## Scripts And Validation

Workflow scripts are deterministic orchestration. Use Python 3.12+ stdlib, inputs/outputs, stable validation, workflow ownership. Run with `python -B` or `PYTHONDONTWRITEBYTECODE=1`. Do not add aliases, shell wrappers, installers, manager-skill behavior, or `__pycache__`.

Scaffold and validate:

```shell
python -B .agents/manage.py new --kind workflow --name <workflow-name> --summary "<summary>" [--uses-skill skill-manager --uses-script ".agents/manage.py compare-skill"]
python -B .agents/manage.py check-additions
python -B .agents/manage.py validate-automations
python -B .agents/manage.py sync-automation-routing --check
python -B .agents/manage.py check
```

Validation covers layout, contracts, commands, related modules, phase IDs, workers, tasks, context-evidence metadata, external access, wrappers, generated freshness, run packets, hooks, diagrams, `__pycache__`.
