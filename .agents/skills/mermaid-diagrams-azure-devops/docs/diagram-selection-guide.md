# Diagram Selection Guide

Choose the smallest Azure DevOps-supported diagram type that matches the question.

| Use case | Prefer | Notes |
|---|---|---|
| Tables, relational data, schemas, entities, cardinalities | `erDiagram` | First choice for data models and table relationships. |
| Process, workflow, repository setup, architecture links | `graph TD;` or `graph LR;` | Use semantic shapes and short quoted labels. |
| API calls, actor interactions, request/response flows | `sequenceDiagram` | Keep participant names short and stable. |
| State, lifecycle, status transitions | `stateDiagram-v2` | Use when states matter more than steps. |
| Class, object, inheritance, interfaces | `classDiagram` | Use for code/object relationships, not database tables. |
| Dates, phases, project schedule | `gantt` | Use ISO dates where possible. |
| Percentages or proportions | `pie` | Keep categories few and labels short. |
| User experience journey | `journey` | Use for satisfaction or friction across steps. |
| Requirements traceability | `requirementDiagram` | Use for requirement, element, and verification links. |
| Git branching or release flow | `gitGraph` | Use for branch, merge, and release history. |
| Chronological events | `timeline` | Use for ordered milestones without durations. |
| Unsure | `graph TD;` | Default fallback, then split if it grows dense. |

Prettier diagrams come from the right type, clean structure, and readable labels, not custom styling. Avoid theme blocks, `classDef`, `style`, `linkStyle`, HTML labels, and icons in repo-authored docs.
