# Testing And Validation

Use this file when proving .NET Framework maintenance changes.

## Command Discovery

- Prefer documented repository commands, CI YAML, build scripts, solution files, and test run settings.
- Use Visual Studio or full MSBuild when old project types require it.
- Use vstest, MSTest, NUnit, or xUnit runners according to the existing suite.
- Do not invent a successful `dotnet test` result for projects that the dotnet CLI cannot run.

## Evidence

- Build: solution or target project, including restore if it is part of normal validation.
- Tests: targeted assemblies or categories that cover the changed behavior.
- Config: diff `app.config`, `web.config`, transforms, binding redirects, and generated service references when touched.
- Runtime smoke: IIS Express start, Windows service start/stop, desktop launch, or installer smoke when relevant.

## Working With Missing Tooling

- Report missing Visual Studio workloads, reference assemblies, targeting packs, IIS Express, SDKs, or test adapters as blocked or skipped validation.
- If existing CI artifacts are available, use them as evidence only when they cover the changed project and commit.
- `dotnet-quality-gates` can parse compatible coverage, TRX, JUnit, static scan, or benchmark artifacts; it does not replace a legacy build.

## Review Checklist

- Test evidence is tied to the changed behavior.
- Missing tools are named precisely.
- Generated files are either intentionally updated or unchanged.
- Validation does not hide failed or skipped legacy prerequisites.
