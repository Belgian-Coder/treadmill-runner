# Framework Assessment

Use this file before changing or migrating .NET Framework code.

## Inventory

- Target frameworks: `net48`, `net472`, older `v4.x`, or mixed projects.
- Project format: old csproj, SDK-style with .NET Framework target, website project, setup project, or custom build.
- Package mode: `packages.config`, PackageReference, manual binaries, GAC, COM, or NuGet restore through legacy targets.
- Host: IIS, IIS Express, Windows service, scheduled task, desktop app, add-in, or console.
- Config: `app.config`, `web.config`, transform files, machine config, binding redirects, connection strings, certificate references, and app settings.
- Tests: MSTest, NUnit, xUnit, vstest, legacy test settings, or no automated suite.

## Risk Signals

- Designer-generated code or `.resx` files edited by hand.
- Web Forms lifecycle, ViewState, custom HttpModules, custom HttpHandlers, or global.asax startup.
- WCF endpoints, bindings, behaviors, generated service references, and config-driven clients.
- COM registration, registry reads, GAC references, native DLL probing, or bitness constraints.
- Build steps that depend on Visual Studio, installed SDKs, IIS metabase, or machine-wide certificates.

## Maintain-In-Place Checklist

- Keep the runtime and hosting model fixed.
- Limit fixes to the failing behavior and nearby tests.
- Preserve binding redirects unless package changes require a reviewed update.
- Capture missing local prerequisites as blocked validation, not as assumed success.

## Migration Readiness

- Identify shared libraries that can move first.
- Find seams around UI, service host, data access, static singletons, and config reads.
- Establish tests around behavior that will move.
- Separate "must keep running" constraints from "can modernize" code.
