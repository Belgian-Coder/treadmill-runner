# State Diagram Template

::: mermaid
    stateDiagram-v2
      [*] --> Draft
      Draft --> Review : submit
      Review --> Draft : changes_requested
      Review --> Accepted : approve
      Accepted --> [*]
:::
