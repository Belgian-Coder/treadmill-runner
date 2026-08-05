# Build And Runtime

Use this file before changing project, package, runtime, or host details in existing .NET Framework systems.

## Inventory

- Target framework: `net48`, `net472`, older `v4.x`, or mixed solution targets.
- Project style: old csproj, SDK-style targeting .NET Framework, website project, setup project, service project, or custom targets.
- Package mode: `packages.config`, PackageReference, manual binaries, GAC, COM, or NuGet restore through legacy targets.
- Build entrypoint: Visual Studio solution build, `MSBuild.exe`, custom scripts, CI task, setup project, or vendor build tool.
- Host: IIS, IIS Express, Windows service, scheduled task, desktop app, COM add-in, or console.

## Runtime Files

- Preserve `app.config`, `web.config`, transforms, machine config assumptions, connection strings, certificate references, and app settings.
- Treat binding redirects as runtime behavior. Package updates can require redirect changes; unrelated edits should avoid them.
- Keep service references, generated proxies, `.settings`, `.resx`, designer files, and embedded resources intact.

## Platform Constraints

- Record bitness, registry access, COM registration, GAC references, native DLL probing, filesystem ACLs, and service account assumptions.
- Do not replace IIS or Windows service hosting with modern hosting unless migration is approved.
- Do not assume installed reference assemblies, targeting packs, SDKs, or Visual Studio workloads are present.

## Review Checklist

- The build command matches the existing project system.
- Package restore and binding redirects remain coherent.
- Config transforms still target the intended environment.
- Runtime prerequisites are listed when validation cannot run locally.
