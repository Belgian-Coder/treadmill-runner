# Harness templates

These seed `harness/templates/` in the new repository (plan section 12). `./gradlew newStory -Pid=<ID>` copies them into `stories/<NNN>-<ID>-<slug>/`.

| Template | Becomes |
|---|---|
| `ticket.template.md` | `ticket.md`, already filled for every planned story in `../stories/` |
| `plan.template.md` | `plan.md`, written by the planner model |
| `packet.template.md` | `packets/P<n>.md`, optional, for detailed packets |
| `execution-log.template.md` | `execution-log.md`, the living checklist |
| `validation.template.md` | `validation/validation.md` |
| `adr.template.md` | `docs/adr/NNNN-<title>.md` via `./gradlew newAdr` |
| `decisions-register.template.md` | `docs/decisions.md` (created once in HAR-01) |
