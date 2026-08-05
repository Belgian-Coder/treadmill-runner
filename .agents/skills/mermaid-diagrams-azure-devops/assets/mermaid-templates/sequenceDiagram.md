# Sequence Diagram Template

::: mermaid
    sequenceDiagram
      participant User
      participant Agent
      participant Repo
      User->>Agent: Request change
      Agent->>Repo: Inspect files
      Repo-->>Agent: Facts
      Agent-->>User: Summary
:::
