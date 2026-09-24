# Execution log — <ID> <title>

Stage: ticket | planned | reviewed | implementing | validating | done
Last updated: <YYYY-MM-DD HH:MM> by <role/model>

The orchestrator updates this file after every step and every packet (plan section 12.7).

## Checklist
- [ ] Parallel read-only searches done (searcher, Haiku)
- [ ] Plan written (orchestrator, Opus 5.5 medium)
- [ ] Blocking open questions answered and recorded as decisions
- [ ] P1 — <goal> — evidence: <commit / build/ai/…json>
- [ ] P2 — …
- [ ] ciFast green
- [ ] ciNightly green (if device or UI behaviour changed)
- [ ] Screens captured (captureScreens / webScreens)
- [ ] UX validation done (ux-validator, Opus 5.5 high; validation/validation.md)
- [ ] ADRs created/updated (docs/adr) for architectural decisions
- [ ] All plan decisions appended to docs/decisions.md
- [ ] User guide updated (docs/user-guide) for user-visible changes
- [ ] Dev guide updated (docs/dev-guide) for new/changed tasks or procedures
- [ ] Maps refreshed (./gradlew aiMaps)
- [ ] Final review passed (final-reviewer, Opus 5.5 high, fresh context)
- [ ] All acceptance criteria checked with evidence
- [ ] storyCheck done + commit

## Deviations
| When | What changed versus the plan | Why | Approved by |
|---|---|---|---|

## Issues found
| ID | Severity (blocker/major/minor) | Description | Status / link |
|---|---|---|---|

## Future improvements
- …
