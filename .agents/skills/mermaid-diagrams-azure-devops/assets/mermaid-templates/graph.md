# Graph Template

::: mermaid
    graph TD;
      start(["Start"]) --> inspect["Inspect context"];
      inspect --> decision{"Decision"};
      decision -->|Yes| write["Write result"];
      decision -->|No| refine["Refine input"];
      refine --> inspect;
      write --> done(["Done"]);
:::
