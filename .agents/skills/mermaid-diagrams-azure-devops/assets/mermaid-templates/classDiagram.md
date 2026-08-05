# Class Diagram Template

::: mermaid
    classDiagram
      class Skill {
        +string name
        +string version
        +validate()
      }
      class Workflow {
        +string id
        +start()
      }
      Skill <|-- ManagerSkill
      Workflow --> Skill : uses
:::
