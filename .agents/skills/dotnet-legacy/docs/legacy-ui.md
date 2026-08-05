# Legacy UI

Use this file for .NET Framework WPF, WinForms, and mixed desktop modernization.

## First Checks

- Identify target framework, CPU bitness, designer dependencies, deployment model, and native or COM references.
- Check whether generated designer files, `.resx`, XAML, app manifest, app config, or installer files are part of the change.
- Confirm UI threading assumptions before moving async work.

## WPF

- Preserve binding paths, commands, resources, styles, and dispatcher usage.
- Prefer extracting view models and services before changing views.
- Be careful with static resources, merged dictionaries, and theme assemblies.
- Validate high-DPI and layout behavior when touching visual trees.

## WinForms

- Preserve designer-generated code ownership; prefer designer-safe edits.
- Keep UI thread access explicit with `Invoke` or synchronization context patterns already used by the app.
- Avoid changing control names, resource keys, or event wiring without verifying designer and runtime behavior.
- Treat high-DPI, anchoring, scaling, and font changes as visual regression risks.

## Migration Options

- Maintain on .NET Framework when dependencies, designers, or deployment cannot move safely.
- Move business logic and services first, leaving UI on the legacy host when needed.
- Consider modern .NET desktop only after validating third-party controls, COM/native dependencies, installer behavior, and target OS support.

## Validation

- Build with the project-supported toolchain.
- Run UI smoke checks manually or through existing automation when visual behavior changed.
- Verify designer load when designer files or resources changed.
- Record missing Windows-only tooling as blocked validation.
