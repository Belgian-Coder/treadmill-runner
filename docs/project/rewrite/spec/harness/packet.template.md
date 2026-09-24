# Packet <ID>-P<n> — <goal in one sentence>

- Role: implementer (Sonnet 5, effort high)
- Depends on: <packets>
- Budget: ≤ <n> changed lines

## Read first (only these)
- `<file>`
- Spec: `docs/spec/<file>.md#<anchor>` (only the named section)

## Allow-list (create or edit only these)
- `<file>`

## Steps
1. …

## Tests to add
- `<test file>`: <cases>

## Done-check
- `./gradlew <task>` → `build/ai/<task>.json` status `passed`
- <any extra assertion>

## Escalate to orchestrator if
- a done-check fails twice, a file outside the allow-list must change, or a spec rule is unclear.
