# Maintenance

Use this file for low-risk fixes and refactors that keep the existing .NET Framework runtime and deployment model.

## Fix Strategy

- Start from the failing behavior and the narrowest owning project.
- Prefer small edits around the fault over broad framework rewrites.
- Keep public APIs, serialization contracts, database shape, routes, WCF contracts, and UI control names stable unless explicitly changed.
- When code is hard to test, add characterization tests or focused smoke evidence before refactoring.

## Dependencies

- Treat package updates as runtime changes because binding redirects, config transforms, and transitive assembly loading can change.
- Avoid opportunistic conversion from `packages.config` to PackageReference.
- Check whether packages are centrally pinned by CI, custom restore scripts, or checked-in package folders.

## Design Boundaries

- Do not introduce modern ASP.NET Core, generic host, or dependency-injection patterns into legacy hosts unless the project already uses them.
- Adapters and seams are useful when they isolate risky code without changing host behavior.
- Keep synchronous APIs when caller contracts require them; avoid sync-over-async rewrites that change threading behavior.

## Common Risks

- Designer files regenerated unexpectedly.
- Binding redirects updated without a package or assembly reason.
- Web.config transform drift.
- WCF client config no longer matches generated service proxies.
- Windows service shutdown/startup behavior changes.
