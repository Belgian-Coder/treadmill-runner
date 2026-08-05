---
title: AI Harness Findings
type: engineering-findings
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# AI harness findings

This append-only log records friction observed while using the repository AI harness. Product defects and ordinary implementation failures belong in their story evidence instead. Do not claim a harness improvement from a single observation; promote recurring findings into validators, workflow tests, or paired benchmarks.

## 2026-08-03 — GPT-5.4 replacement benchmark

- The benchmark skill had grown to 1,420 trigger-loaded words while its eval still expected version 1.0.0 and an obsolete command list. The skill is now 757 words, the eval matches version 1.3.0/current commands, and all four eval groups pass. This is a concrete example of preferring a smaller authoritative entry point over raising the token budget to hide drift.
- Direct Terra-max and Luna-max were not replacements: both failed locked restore/browser publication with NU1004; Terra-max cost 284.776660 credits over 78.6 minutes, while Luna-max cost 38.903586 credits but took 94.3 minutes and used 58.3 million tokens.
- Open-ended review was also ineffective: broad Terra-high review brought the route to 114.257300 credits and remained ineligible; broad Sol-medium review brought it to 340.339780 credits and still left package-lock drift.
- The winning pattern was conditional targeted repair. Terra-high implementation plus an exact deterministic failure packet plus Luna-high repair passed locked validation and Playwright 19/19 at 62.292776 total credits/$2.491711 over 34.3 minutes.
- Targeted Sol-medium repair reached the same 100/100 result at 111.405480 credits/$4.456219 with similar wall time. Sol did not earn its extra cost for this clear non-safety packet.
- Targeted Terra-medium passed locked validation but left a 36px phone target, so it was ineligible.
- Preferred provisional policy: keep planning and implementation in one Terra-high task; stop after a green full gate; otherwise dispatch the exact bounded failure packet to Luna-high and rerun the full gate. Escalate to Sol only for high-consequence ambiguity or after Luna fails. Do not use routine planners, open-ended reviewers, or direct max reasoning.
- The legacy scorer still includes candidate-authored text/test heuristics. Hard eligibility is anchored to actual locked validation and browser commands; a route-blind inspection separately confirmed the known failures. Future protocol should inject evaluator-owned tests.

## Entry format

Each finding records the command or workflow that exposed it, its impact, the temporary workaround, and a candidate improvement. Keep the original entry after a fix and add its resolution evidence.

## Findings

### HARNESS-001 — Workflow state does not advance from implementation activity

- Observed: 2026-08-02 during TR-002.
- Status: mitigated; completion and plan-derived state are fixed, but phase selection remains conservative.
- Evidence: after an approved 32/32 `workflow plan-check` and `workflow resume`, `run.json` still reported `current_phase: orientation` and `phase.status: not-started`, while the resume packet correctly selected `WP1` as the next action.
- Impact: the canonical state can lag behind the actual story, so handoff or finish checks may require manual reconciliation and can mislead a fresh agent.
- Workaround: treat the plan's work-package status and direct validation evidence as authoritative during implementation, then explicitly reconcile `run.json`, `REPORT.md`, and the execution log before finish.
- Improvement candidate: add a deterministic `workflow phase begin/complete` command, or have resume/checkpoint derive phase state from approved plan work-package transitions without parsing free-form prose.
- Repeated evidence: the TR-003 `workflow finish` command returned `ok: true` after every proof gate passed, but the run remained `status: partial` and the first refreshed index still classified it as partial until canonical status was reconciled manually. This strengthens the case for lifecycle commands owning the state transition they validate.

### HARNESS-002 — Completion context can exceed the workflow budget

- Observed: 2026-08-02 while finishing TR-001.
- Status: mitigated in TreadmillRunner; bootstrap normalization remains a repository concern.
- Evidence: the TR-001 workflow finish required multiple context-budget reductions before it passed; the installed harness also emits advisory size warnings.
- Impact: completion spends extra turns compressing evidence and risks dropping useful acceptance or residual-risk detail.
- Workaround: store raw command output behind evidence paths and keep `REPORT.md` and `run.json` to compact outcomes rather than pasted logs.
- Improvement candidate: enforce per-section size budgets before finish, identify the largest fields in the failure, and offer a deterministic compaction command that preserves required completion fields.

### HARNESS-003 — Workflow routing overlap is detected late

- Observed: 2026-08-02 while creating `treadmillrunner-delivery`.
- Status: open.
- Evidence: initial scaffolding classified the workflow as new, while the richer validation later detected overlap with `user-story-workflow`.
- Impact: a project can invest in a specialized workflow before learning that routing boundaries are ambiguous.
- Workaround: document the project-specific safety/evidence boundary and retain the specialized workflow only after strict validation confirms the distinction.
- Improvement candidate: run similarity/ownership checks during proposal or scaffold preview and require an explicit extension-versus-new decision before files are created.

### HARNESS-004 — Repository instruction conflicts with the approved Windows script strategy

- Observed: 2026-08-02 during TR-001/TR-002.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: generated `AGENTS.md` says not to use active PowerShell wrappers, while the approved Windows-first plan explicitly requires deterministic PowerShell entry points under `eng/`.
- Impact: agents can follow the repository's platform plan yet appear to violate the installed harness instructions, producing inconsistent reviews and routing.
- Workaround: treat the explicit project plan as the narrower authority and keep PowerShell scripts deterministic, strict, and documented.
- Improvement candidate: make the harness language rule profile-aware, or let installation record approved repository script languages and generate matching instructions.

### HARNESS-005 — Restored NuGet lock files fail the harness line-ending gate

- Observed: 2026-08-02 during TR-002 quality validation.
- Status: open.
- Evidence: `eng/bootstrap.ps1` successfully refreshed the WebAssembly dependency lock, after which the harness line-ending check rejected `src/TreadmillRunner.Web/packages.lock.json` for CRLF and a missing final newline.
- Impact: the documented dependency-refresh path can immediately make the generic quality packet fail even though restore and locked restore are correct.
- Resolution evidence: the deterministic line-ending fixer normalized the lock file to CRLF with a final newline. The changed-only gate now passes with 476 Git-visible files checked, 401 non-matching files skipped, and zero failures.
- Improvement candidate: let the repository profile declare generated-file normalization, or have the line-ending gate honor `.gitattributes` and report an exact safe fixer command for generated lock files.

### HARNESS-006 — Workflow lifecycle commands accept inconsistent output flags

- Observed: 2026-08-02 while finishing TR-002.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: `workflow resume` accepted `--summary --compact --format json`, but applying the same output-shaping flags to its suggested `workflow finish` command failed because `finish` accepts only `--format`.
- Impact: agents cannot safely reuse the harness's normal compact-output convention across lifecycle commands and spend an avoidable retry discovering command-specific parser differences.
- Workaround: run `workflow finish --name <name> --run-id <id> --format json` without `--summary` or `--compact`.
- Improvement candidate: standardize harmless output-shaping flags across lifecycle commands, or have every generated `next_command` include the exact supported arguments.

### HARNESS-007 — Finish rejects the unsupported claims required by the workflow contract

- Observed: 2026-08-02 while finishing TR-002.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: the workflow completion contract requires unsupported claims to be stated, but `workflow finish` returned `ok: false` with the sole issue `run packet records unsupported claims` after all proof gates passed.
- Impact: accurate limitations prevent run completion, encouraging agents either to omit important caveats or duplicate them outside canonical state.
- Workaround: preserve unsupported claims in `REPORT.md`, record this harness finding, and clear only `run.json.unsupported_claims` before rerunning finish.
- Improvement candidate: treat unsupported claims as required disclosure and fail only when completion language contradicts them; alternatively introduce separate acknowledged/resolved/deferred claim states.

### HARNESS-008 — Checkpoint plan parser reports completed work as pending

- Observed: 2026-08-02 during the successful TR-002 finish.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: all `Bounded Work Packages` rows in `plan.md` were marked `complete`, but the generated checkpoint selected WP1 as `next_unblocked_package` with status `pending`.
- Repeated evidence: the final TR-003 finish regenerated the same false WP1 `pending` recommendation immediately after `workflow plan-check` passed 36/36 and all six package rows were `complete`.
- Impact: resume packets can direct a new agent to repeat already completed work even when the plan hash is current and finish proof is green.
- Workaround: trust the reviewed plan row, execution log, and report rather than the checkpoint's derived next package for a finished run.
- Improvement candidate: align the plan parser's accepted status vocabulary with the workflow template and add a regression fixture for `complete` work-package rows.

### HARNESS-009 — Shared-worktree validation collides with active agent builds

- Observed: 2026-08-02 during TR-003 parallel implementation.
- Status: mitigated by repository instructions; isolated output or a deterministic lease is still a possible extension.
- Evidence: `eng/playwright.ps1 -Configuration Release` passed readiness and the test-project build, then its Gateway publish failed with `CS2012` because another agent's concurrent build held `src/TreadmillRunner.Gateway/obj/Release/.../TreadmillRunner.Gateway.dll`. An earlier solution build also emitted retry warnings while an agent-owned test host held the IntegrationTests assembly.
- Impact: deterministic validation can fail for orchestration reasons unrelated to source correctness, producing noisy retries and ambiguous evidence when agents share one worktree and output directories.
- Workaround: wait for implementation agents to finish their build/test commands before running repository-wide validation, then rerun from a quiet worktree.
- Improvement candidate: add a repository-scoped validation/build lease to the workflow harness, or assign isolated `BaseOutputPath`/`BaseIntermediateOutputPath` values per agent and reserve the standard paths for final validation.

### HARNESS-010 — Local quality line-ending scan includes ignored build artifacts

- Observed: 2026-08-02 during the TR-003 completion quality packet.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: `validate_local_quality.py --target .` reported line-ending failures under the gitignored `artifacts/client-*` and `artifacts/e2e-host` publish directories, including SDK-generated runtime JSON and `dotnet.js`. The committed source had already passed `eng/validate.ps1` formatting.
- Impact: a normal published-host browser validation makes the later generic quality packet fail on generated files that are neither reviewed nor committed.
- Workaround: run the packet with `--line-endings-changed-only` and keep publish output under ignored paths; normalize committed generated lock files separately.
- Improvement candidate: make `validate_line_endings.py` honor `.gitignore` by default, with an explicit opt-in for ignored/generated artifacts when they are the intended target.

### HARNESS-011 — Standard-profile documentation contains links to uninstalled harness pages

- Observed: 2026-08-02 during the TR-003 completion quality packet.
- Status: resolved in the unreleased reusable harness working tree and installed standard profile.
- Evidence: the docs link check found 43 missing local targets from installed harness pages such as `docs/start-here.md`, `docs/reference/documentation-map.md`, and `docs/harness/setup.md`; the referenced pages were not installed by the selected `standard` harness profile.
- Impact: repository-wide documentation validation cannot pass even when all project-owned documentation links are valid, and the failure does not identify the harness installation profile as the source.
- Workaround: link-check `docs/project` for project delivery evidence and retain the repository-wide failure in this findings log.
- Improvement candidate: make each installation profile self-contained, rewrite unavailable links during installation, or scope the generated link-check manifest to pages present in that profile.

### HARNESS-012 — Changed-only quality checks require an existing Git commit

- Observed: 2026-08-02 during the TR-003 completion quality packet in the newly initialized repository.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: rerunning `validate_local_quality.py` with `--line-endings-changed-only` failed with `fatal: ambiguous argument 'HEAD'` because the repository has no initial commit yet.
- Impact: the recommended way to avoid ignored generated artifacts cannot be used during repository foundation work, precisely when all files are intentionally untracked.
- Workaround: validate explicit source and test roots independently until an initial commit exists, and retain the repository-wide limitation in the evidence packet.
- Improvement candidate: when `HEAD` is absent, derive changed files from `git ls-files --cached --others --exclude-standard`, or emit a structured skip/fallback instead of a raw Git failure.

### HARNESS-013 — Optional missing outputs make successful finish evidence look incomplete

- Observed: 2026-08-02 during the final TR-003 finish.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: `workflow finish` returned `ok: true`, an empty `missing_proof` list, and zero required missing outputs, but its `evidence_completeness.status` was still `needs-evidence` because five explicitly optional artifact files were absent.
- Impact: automation consuming the evidence status can classify a successfully finished run as incomplete even though the finish contract treats every omission as non-blocking.
- Workaround: use `ok`, `proof_matrix.ok`, `missing_proof`, and `required_missing_count` together instead of treating `evidence_completeness.status` as the completion result.
- Improvement candidate: report optional omissions as `complete-with-advisories`, or reserve `needs-evidence` for required proof gaps and expose optional suggestions in a separate advisory field.

### HARNESS-014 — Natural-language routing misses the exact project workflow

- Observed: 2026-08-02 while starting TR-004.
- Status: resolved in the unreleased reusable harness working tree and project workflow metadata.
- Evidence: `workflow start --from-request` returned `confidence: none` and `no workflow route matched the request` for a request naming TR-004, TreadmillRunner, simulator UX, Playwright, and the no-remote-Start boundary, although `automations/routing.md` contains the exact `treadmillrunner-delivery` owner.
- Impact: the mandatory route-first command cannot start an obvious project workflow and forces an otherwise unnecessary retry with an explicit workflow name.
- Workaround: inspect `automations/routing.md`, select `treadmillrunner-delivery`, and run `workflow start --name treadmillrunner-delivery`.
- Improvement candidate: add workflow-name/project-name aliases to the deterministic router and a regression fixture containing a TreadmillRunner story identifier plus its safety vocabulary.

### HARNESS-015 — Lifecycle retrieval consumes large raw context for a bounded story

- Observed: 2026-08-02 while starting and resuming TR-004; the owner also reported unexpectedly high Codex-plan usage.
- Status: clarified and mitigated; exact provider billing remains unmeasured.
- Evidence: the compact `workflow start` packet reported `raw_context_tokens_estimated: 19879`; the first compact `workflow resume` reported `raw_context_tokens_estimated: 24453` and `status: needs-attention`, although the user-facing packets were only about 658 and 754 estimated output tokens. The run already had an explicit workflow owner and a decision-complete plan.
- Impact: hidden retrieval/checkpoint processing can dominate token usage before product implementation begins, particularly when repeated lifecycle commands are prescribed.
- Workaround: use the explicit workflow name, load only human-readable owner files, avoid repeated resume/checkpoint/context calls, disable advisory local-AI work, and run one final lifecycle gate after project-native tests.
- Improvement candidate: enforce an input-context budget before lifecycle retrieval, reuse unchanged evidence by hash, offer a truly lean no-checkpoint run profile, and report estimated input/context cost before executing optional retrieval.

### HARNESS-016 — Local AI can regress small or policy-disabled tasks

- Observed: 2026-08-02 while auditing whether TreadmillRunner actually used the local model.
- Status: resolved for bounded structured changed-file packets and policy-disabled tasks; unstructured model tasks remain benchmark-gated.
- Evidence before the fix: an explicit small `changed-files-summary` invoked Nemotron 3 Nano 4B on CPU, took 28,358 ms, and misattributed one supplied risk line; an exact cache hit returned in about 0.8 seconds. A policy-disabled `validation-triage` still spent 17,577 ms preparing local-AI state before refusing the task.
- Evidence after the first fix: a unique three-line changed-file packet used `deterministic-small-input`, reported `model_invoked: false` and `inference_ms: 0`, and completed in 304 ms wall time. A unique disabled validation packet reported `policy-disabled`, `attempt_count: 0`, `model_invoked: false`, and completed in 306 ms.
- Larger paired probe: a unique 20-line packet really invoked Nemotron 3 Nano 4B (`model_invoked: true`) and took 49,477 ms wall/48,758 ms inference, but misleadingly summarized only files 1–5. After extending the deterministic structured-input route, the equivalent unique packet completed in 305 ms with zero inference, reported all 20 input rows plus the bounded-output omission count, and retained representative path evidence.
- Project result: `local-ai status --summary --compact --json` reports `enabled: false`, `installed_count: 0`, `cache_total: 0`, and no selected model. TreadmillRunner has not automatically used local AI.
- Decision: do not auto-wire local AI into TreadmillRunner workflows. Structured changed-file packets use deterministic processing. Retain model use only for explicit unstructured evidence after a paired quality, latency, and cleanup benchmark proves an advantage.

### HARNESS-017 — Harness update setup reinitializes an existing project

- Observed: 2026-08-02 while installing the corrected standard profile into TreadmillRunner.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: `install-harness --run-setup-check` updated an existing installation, then ran write-mode `setup` before `setup --check`. That created a duplicate `docs/project/generated-2` context tree and made navigation stale.
- Impact: a validation-sounding update flag can add project-owned generated context, lengthen an update, and require cleanup.
- Resolution: an update now skips `setup-initialize` and runs only `setup --check`; fresh installations retain initialization. A focused regression test proves the update command list contains only the check. The accidental `generated-2` tree was removed and navigation was refreshed to `fresh`.

### HARNESS-018 — Finish diagnostic can hide the current input mutation behind a stale receipt

- Observed: 2026-08-02 during the exhaustive skill-manager regression run.
- Status: resolved in the unreleased reusable harness working tree.
- Evidence: changed-scope validation was correctly rerun after a pre-validation phase changed the input fingerprint, but `validation_reuse.reason` retained `receipt is older than the maximum reuse age` instead of naming the current phase-selection mutation.
- Impact: execution was safe, but the misleading primary reason made troubleshooting harder and caused the dedicated input-stability regression to fail once its fixed-date receipt aged past 24 hours.
- Resolution: phase-selection instability now takes diagnostic precedence regardless of whether an older receipt was otherwise reusable. The focused regression and the complete 729-test skill-manager suite pass.

### HARNESS-019 — Model routing was reusable but not project-owned by task

- Observed: 2026-08-02 while designing lower-cost multi-agent orchestration for TreadmillRunner.
- Status: resolved in the unreleased reusable harness working tree and installed TreadmillRunner standard profile.
- Evidence before the fix: `worker_profiles.json` contained reusable semantic profiles and host route sets, but a project could not assign an ordered Codex, GitHub Copilot, or Claude model fallback chain to a concrete task or task set without editing harness-owned data. Named agents would have duplicated responsibilities and forced unnecessary delegation structure.
- Impact: the primary orchestrator lacked one compact, project-owned answer for “what is this task responsible for, should execution stay deterministic/primary or may it be delegated, and which model should be tried next after failure?” Provider availability could also be confused with a declared preference.
- Resolution: added the portable `orchestration.md` contract, project-owned `.agents/orchestration.json`, a strict schema, and deterministic `workflow route-model`. Exact task configuration wins over its task set, unknown tasks use the configured default set, unknown hosts use the chain default, and every host chain must end in `active` or `inherit`. The command reports preferences without claiming availability, selects only when `--available-model` is supplied, and advances past `--failed-model` without retrying it.
- Portability: generated Codex/Copilot/Claude instruction surfaces point through `AGENTS.md` to the same orchestration contract. TreadmillRunner declares separate cost-first, balanced, and high-consequence chains plus deterministic validation and primary-integration task sets. GitHub Copilot model identifiers come from its documented CLI surface but remain guarded by the active-model fallback because availability varies by plan, client, and version.
- Validation: eight focused fallback/validation tests and the complete 274-test workflow-manager suite pass in the separate harness-source checkout; the harness copy contract passes; the standard profile installs into TreadmillRunner with zero collisions and preserves the project-owned route file.

### HARNESS-020 — Navigation refresh rewrites harness-owned metadata to an older date

- Observed: 2026-08-02 while refreshing TreadmillRunner navigation after installing task orchestration.
- Status: resolved in the unreleased reusable harness working tree and corrected in TreadmillRunner.
- Evidence: `repo_navigation.py install --target . --write` changed `automations/navigation/metadata/workflow-metadata.json` from the installed source value `2026-07-25` to a hard-coded generator value `2026-07-01`. The next safe `install-harness` run blocked with one collision instead of overwriting the target.
- Impact: an ordinary deterministic navigation refresh made a harness-owned input diverge and prevented future safe harness updates; using `--force` would have hidden the generator defect.
- Resolution: aligned the reusable navigation generator with the canonical metadata date, added a focused assertion for generated metadata, corrected the project file explicitly, and retained normal collision protection.

### HARNESS-021 — EF migration generation produces immediate repository-format debt

- Observed: 2026-08-02 during TR-004 session-persistence work.
- Status: open; project-side workaround proven once, not yet promoted to the reusable harness.
- Evidence: `eng/database.ps1 -Action Add` generated the migration, designer, and snapshot successfully, but the next repository validation reported charset, end-of-line, indentation, and whitespace failures in those generated files. Running the project formatter normalized them and the full validation passed.
- Impact: the documented deterministic migration command can leave the repository in a predictably failing quality state and adds a repair cycle after every schema change.
- Workaround: run `dotnet format` after migration generation and review that only the intended migration artifacts changed.
- Improvement candidate: make the project database wrapper normalize and verify newly generated migration artifacts before reporting success. Promote a reusable harness change only if another project or a stable harness fixture reproduces the same mismatch.

### HARNESS-022 — Workflow state remained at intake after implemented work packages

- Observed: 2026-08-02 during TR-004.
- Status: repeated evidence for HARNESS-001/HARNESS-008; no additional reusable patch applied in this run.
- Evidence: after Core, persistence, Gateway, Web, integration, and Playwright work had passed project-native validation, the canonical run still reported `current_phase: context-intake`, with WP2–WP5 pending and an orientation-era report/log.
- Impact: a resumed agent would be directed toward already completed intake and implementation work unless it reread source and validation evidence.
- Workaround: reconcile the plan, report, execution log, and run packet once after project-native validation instead of invoking high-context lifecycle retrieval during each implementation step.
- Improvement candidate: retain this as a regression case for deterministic phase transition commands and terminal plan-row precedence. Existing reusable fixes must be revalidated against this live TR-004 run before declaring the recurrence resolved.

### HARNESS-023 — Natural-language project routing regressed for a new TreadmillRunner story

- Observed: 2026-08-02 while starting the capability-gated Start and generic treadmill-adapter work.
- Status: open; recurrence of HARNESS-014 with a different request fixture.
- Evidence: `workflow start --from-request` returned `confidence: none` and `no workflow route matched the request` even though the text named TreadmillRunner, Horizon Omega Z, Domyos Run 500/Challenge Run, protocol capabilities, and implementation. Explicit `workflow start --name treadmillrunner-delivery` succeeded immediately.
- Impact: the documented route-first path adds a failed command and manual owner selection for an unambiguous project request.
- Workaround: use the human routing index and explicitly start `treadmillrunner-delivery`.
- Improvement candidate: add this exact multi-model/protocol wording as a router regression fixture and verify that project-name anchoring survives punctuation, model names, and safety qualifiers.

### HARNESS-024 — Generated delivery plan omits sections required by its own validator

- Observed: 2026-08-02 in run `20260802-180005`.
- Status: open.
- Evidence: `workflow start --name treadmillrunner-delivery` generated `plan.md` from the workflow's default template, then `workflow plan-check` failed with eleven missing required sections (`Out Of Scope`, `Context Evidence`, `Project Context`, impact/security/persistence/diagram/UI/quality/validation/approval). After those were added, a second pass exposed exact table-column contracts that the generated template also did not provide. A manually completed plan then passed 36/36.
- Impact: every new run can require two avoidable repair cycles before approved implementation, increasing context and token cost while making a generated artifact look start-ready when it is not.
- Workaround: copy the accepted section/table shapes from a prior run, fill them, and run `workflow plan-check` until clean.
- Improvement candidate: make the workflow's resolved default template pass structural plan validation before user content is filled, and add a self-test that scaffolds a run, fills only placeholders, and proves the generated schema matches `plan-check`.

### HARNESS-025 — Project workflow duplication and timestamp run IDs obscured story ownership

- Observed: 2026-08-02 during review of retained TreadmillRunner AI-run folders.
- Status: resolved in the reusable harness source and TreadmillRunner; the broader per-run artifact-volume concern remains a future harness simplification opportunity.
- Evidence before the fix: the project had both `user-story-workflow` and a project-specific `treadmillrunner-delivery` workflow, seven timestamp-named run folders, and 123 retained run files. The custom workflow duplicated the story lifecycle without creating a distinct reusable boundary.
- Impact: story evidence was split across two owners, chronological folder names hid the ticket identity, generated context/checkpoint mirrors added navigation and token cost, and routing fixes targeted an owner that should not have existed.
- Resolution: removed the redundant project workflow; consolidated retained work under `user-story-workflow`; renamed runs to stable `US-<identifier>` folders; reduced retained project run evidence to plan/report/state plus active execution logs. The reusable public lifecycle now requires an identifier for story and bug starts, normalizes stories to `US-<identifier>` and bugs to `BUG-<identifier>`, rejects opposite prefixes, and leaves dates inside run files. Non-ticket workflows retain timestamp defaults.
- Validation: the complete workflow-manager self-test suite passes in the separate harness-source checkout; project behavior is validated again after synchronization. Existing routing findings that mention `treadmillrunner-delivery` remain as historical evidence and are superseded by this resolution.

### HARNESS-026 — Routine multi-model review added cost without measured story quality

- Observed: 2026-08-03 against a frozen copy of the real `US-SAFETY-LIMITS-001` Core story.
- Status: measured once; preferred configuration updated, automatic routing not promoted.
- Evidence: all five requested routes passed visible tests, 15/15 withheld acceptance cases, and protected-baseline checks. Direct Sol-medium completed in 171.964 seconds for 14.404200 credits. Reviewed multi-stage routes took 445.334–525.732 seconds and 17.165307–24.414465 credits.
- Reviewer comparison: Terra-high and Luna-high passed the hidden suite before review. From identical post-Luna bytes, Sol-medium and Sol-high final reviewers changed no files and both passed 15/15; Sol-high added 38.930 seconds and 0.666175 credits.
- Impact: unconditional planning/review stages repeat large fixed context and can consume more time and credits even when deterministic acceptance is already green.
- Decision: default to one direct Sol-medium agent for the best measured latency/cost combination. For cost-first work, test one compact Sol plan followed by Terra/Luna implementation and deterministic acceptance; skip the second Sol turn unless acceptance fails or consequence justifies it. Before final review, the measured Sol→Terra route saved 6.5% and Sol→Luna saved 40.3%, while unconditional Sol review erased those savings. Keep Sol-high as targeted escalation and require three matched repetitions before promoting another route.
- Evidence location: reusable method and conclusion live in `.agents/skills/agent-benchmarking/docs/real-story-routing-benchmark.md`; detailed disposable receipts and TRX output remain in the operator's separate benchmark workspace.

## Resolution evidence — 2026-08-02

- Workflow lifecycle: terminal plan rows override stale run-task status; successful completion promotes the canonical run to `completed`; blocked runs remain `partial`; optional missing outputs are advisories; unsupported claims are disclosures; `workflow finish` accepts `--summary --compact`.
- Routing: a specific activation anchor contributes toward the threshold. The original TreadmillRunner request now selects `treadmillrunner-delivery` with high confidence, score 2, threshold 2, and no confirmation requirement.
- Context accounting: the current resume packet reports a 24,499-token raw reference inventory, a 2,768-token effective load, `raw_reference_inventory_is_loaded: false`, and an 88.7% effective-load reduction. These are rough context estimates, not Codex billing data.
- Quality profiles: changed-only line-ending checks work without `HEAD`, honor Git visibility and ignore rules, and no longer scan ignored publish output. The project lock file was normalized deterministically; the changed-only reproduction now passes with 476 checked, 401 skipped, and zero failures.
- Standard install: `accepted-skills` now depends on `reference-guides`; the standard copy contract passes with 580 candidate files and zero missing roots/excludes. The TreadmillRunner reinstall completed with zero collisions, and a subsequent dry run planned zero files.
- Validation: workflow-manager, local-ai-helper, and dotnet-quality-gates complete owner suites passed; the exhaustive skill-manager suite passed all 729 tests. Generated sync, addition acceptance, agent compatibility, copy-contract validation, and `git diff --check` also passed.

## Promotion rules

- Harness fixes are authored and validated in the separate harness-source checkout, which is the reusable source of truth; do not patch only this repository's installed `.agents` copy.
- After a fix passes in the harness-source checkout, reinstall or synchronize the accepted harness into TreadmillRunner and rerun the original repository reproduction before marking the finding resolved.
- A repeated finding should become a harness issue, validator, eval case, or workflow test with stable reproduction steps.
- Benchmark claims require comparable task boundaries and normalized evidence; this log alone does not establish token, time, cost, or quality improvements.
- Resolutions must name the harness version or commit, verification command, and result.

## 2026-08-03 hard full-stack story benchmark

### HARNESS-027 — Workflow closeout can dominate a completed feature

- In `US-TR-008`, the shared Sol-medium planning stage alone consumed 2,220,315 tokens, 56.809125 credits, 424.103 seconds, and $2.272365 API-list-price equivalent before implementation.
- Direct GPT-5.4 completed the product slice and full validation, then repeatedly compressed workflow evidence and exited with a final token ledger but no `task_complete`: 24,507,827 tokens, 260.713638 credits, 4,884.829 seconds, and $10.428546 equivalent.
- Direct Luna completed its project tests, then spent additional closeout turns on formatting, Mermaid, proof duplication, lifecycle fields, and a context packet that still failed at 3,397 tokens versus the 2,500-token limit. Its full route reached 30,118,712 tokens despite costing only 21.116880 credits/$0.844675 equivalent.
- Decision: prefer source, the project-native full gate, screenshots, and one concise report. Avoid repeated lifecycle/context calls and a separate planner for an already decision-complete story.
- Status: workflow simplification remains open; detailed evidence remains in the operator's separate benchmark workspace.

### HARNESS-028 — Interrupted rollout accounting

- The Codex child can exit while the outer meter remains alive or its final workflow tool call is pending.
- `recover-rollout --allow-incomplete` now requires an explicit check that Codex exited and records `interrupted`, never `completed`.
- The fix is applied to the harness-source checkout, the benchmark workspace, and this installed project copy. All three meter self-tests pass.

### HARNESS-029 — Review cannot replace the full gate

- Direct Terra left a migration-count regression. A fresh GPT-5.4 reviewer found and fixed a real missing Calendar summary but missed that regression because it ran focused tests. Luna-low changed evidence only and found no product defect.
- Direct Sol and Luna also passed their authored browser scenarios while leaving the Calendar forecast stale after calendar mutations; their visible suites did not cover that acceptance transition. Luna's final locked restore/publish also failed after browser-generated dependency drift.
- Decision: implementation → complete deterministic validation → conditional fresh review with the exact failure/story packet → complete validation again.

### HARNESS-030 — Lexical evaluator false negatives

- Exact route strings, migration filenames, and numeric spelling incorrectly penalized valid route-group composition, `AddTrainingPlanning`, and `1_200`.
- The evaluator now compares migrations with the frozen baseline, recognizes route composition/equivalent constants, and can reuse captured command evidence after scoring-only fixes.
- Decision: black-box behavior and executable gates outrank naming/string heuristics.

### HARNESS-031 — Shared high-cost planning regressed full-story cost

- The Sol plan added 56.809125 credits/$2.272365 equivalent to both delegated routes, repeated context in another cache, and did not make either candidate pass the hard gates.
- Decision: no routine planner handoff. Keep coherent implementation in one persistent thread; delegate only bounded independent work or conditional review/repair.
