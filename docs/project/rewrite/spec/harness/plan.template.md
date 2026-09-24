# Plan — <ID> <title>

Status: draft | reviewed (reviewer: <model/role>, date)

## 1. Current state
<What exists today in the repository for this story. Verified by reading code: modules, files, tests. What is missing.>

## 2. Approach
<The chosen design in a few paragraphs. Alternatives considered, in one line each.>

## 3. Diagrams
### 3.1 Change in progress
```mermaid
flowchart LR
  %% new/changed parts styled with :::changed
```
### 3.2 Sequence (if behaviour spans components)
```mermaid
sequenceDiagram
```
### 3.3 Database changes
```mermaid
erDiagram
```
Migration notes: <Room version N → N+1, columns added, backfill, forward-only>. Write "No database changes" when none.

## 4. Packets
| ID | Goal | Allow-list | Done-check | Budget | Tier | Depends |
|---|---|---|---|---|---|---|
| P1 | … | `path/…` | `./gradlew :module:test` (`build/ai/…json` green) | ≤ 300 lines | packet-executor | — |

<Per packet, if more detail is needed: packets/P<n>.md from packet.template.md>

## 5. Decisions log
| ID | Decision | Source | Rationale | Date |
|---|---|---|---|---|
| D1 | … | owner answer / model reasoning | … | YYYY-MM-DD |

## 6. Open questions (answer before implementing)
| ID | Question | Blocking | Answer | Answered by / date |
|---|---|---|---|---|
| Q1 | … | yes/no | | |
