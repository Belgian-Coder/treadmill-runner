# GitGraph Template

::: mermaid
    gitGraph
      commit id: "Initial"
      branch feature
      checkout feature
      commit id: "Skill update"
      checkout main
      merge feature id: "Merge update"
      commit id: "Release"
:::
