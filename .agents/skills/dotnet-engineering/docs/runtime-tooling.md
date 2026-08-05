# Runtime Tooling

Use for source generation, analyzer authoring, serialization generation, native/WASM interop, and runtime or language-version gates.

## Source Generation

- Prefer built-in generators when they remove reflection, improve startup, or make trimming/AOT behavior explicit: System.Text.Json source generation, `JsonSerializerContext`, `GeneratedRegex`, and `LoggerMessage`.
- Generated members need exact compiler-expected `partial` shapes; validate with the compiler, not memory.
- Use custom `IIncrementalGenerator` only when generation has clear maintenance payoff. Keep pipelines deterministic, avoid global mutable state, avoid undeclared file reads, and report diagnostics for unsupported input.
- Test generated output through compile diagnostics and stable snapshots/golden files.
- Keep generated files out of hand-authored edits unless the project intentionally commits generated source.

## Serialization

- Use `System.Text.Json` for ordinary JSON and source-generated contexts for trimmed, AOT, mobile, browser, or high-throughput paths.
- Use Protobuf when the repo owns stable binary contracts, schema evolution, and cross-language or gRPC compatibility.
- Use MessagePack only when binary payloads, versioning, and resolver policy are under test.
- Do not replace Newtonsoft.Json until required converters, polymorphism, reference handling, date formats, and naming policies are checked.

## Native, WASM, Analyzers

- Prefer `LibraryImport` for modern source-generated P/Invoke when supported; keep `DllImport` for existing code, unsupported signatures, or compatibility constraints.
- Use `SafeHandle` for native resource ownership instead of raw `IntPtr`.
- Make marshalling explicit: encoding, struct layout, fixed-width integers, callbacks, ownership transfer, and platform-specific library names.
- Use `NativeLibrary.SetDllImportResolver` only when runtime library selection is necessary and covered by platform tests.
- Route .NET Framework COM, GAC, registry, or old runtime constraints to `dotnet-legacy`.
- For WebAssembly interop, treat `JSImport`, `JSExport`, workloads, browser threading, AOT, and static assets as project/version-specific; check current official docs before setup changes.
- Author Roslyn analyzers only when the repo owns analyzer packages, diagnostic IDs, severities, code fixes, tests, and version compatibility.
- Test analyzers with real diagnostic markup and target compiler versions when the package supports them.

## Version Gates

- Confirm target framework, SDK, `LangVersion`, workload availability, and package versions before current/preview features.
- Do not introduce preview features, workloads, or SDK changes unless the project already opts in or the user explicitly approves.
