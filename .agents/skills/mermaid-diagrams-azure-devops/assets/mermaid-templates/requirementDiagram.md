# Requirement Diagram Template

::: mermaid
    requirementDiagram
      requirement skill_validation {
        id: "REQ-001"
        text: Skills must validate locally
        risk: medium
        verifymethod: test
      }
      element validator {
        type: script
      }
      validator - satisfies -> skill_validation
:::
