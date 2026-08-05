# Sample Consumer Project

This fixture is a small target-project shape for install, workflow, and .NET planning checks. It is not a generated template and should stay free of `.git`, workflow runs, model caches, tool caches, and local secrets.

Use it when a test needs a project with:

- a project-context document
- a solution-like `src/` layout
- central NuGet package management
- a repo-local `NuGet.config`

The fixture is intentionally tiny so harness checks can copy it into a temporary folder and run deterministic validations without external services.
