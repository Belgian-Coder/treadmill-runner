# Gantt Template

::: mermaid
    gantt
      title Delivery Plan
      dateFormat YYYY-MM-DD
      section Preparation
      Inspect context :done, prep1, 2026-01-01, 2d
      Prepare plan :active, prep2, after prep1, 2d
      section Delivery
      Implement :del1, after prep2, 4d
      Validate :del2, after del1, 1d
:::
