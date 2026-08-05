# UI Frameworks

Use this file for modern .NET UI framework choices and reviews.

## Blazor

- Identify hosting model and render modes before changing components: Server, WebAssembly, Hybrid, Auto, static SSR, and interactive modes differ in state, latency, auth, and deployment.
- Enhanced navigation and streaming rendering change lifecycle and loading; test navigation, forms, and state across full and enhanced transitions.
- Use QuickGrid when data shape, paging, virtualization, and accessibility fit; do not force heavily customized grids.
- Keep JS interop scoped, cancellable where possible, and disposed with component lifetime.

## MAUI And Uno

- For MAUI, preserve existing XAML, MVVM, Shell navigation, dependency injection, and platform-service patterns.
- Use CommunityToolkit.Mvvm when generated observable properties and commands fit; validate generator output with build evidence.
- Native AOT and trimming can change reflection, serialization, binding, and startup; treat them as delivery constraints.
- For Uno, keep `UnoFeatures`, Extensions, MVUX, Toolkit, and target heads aligned. Platform-specific code must have target-specific validation.

## Windows Desktop

- WinUI work must respect Windows App SDK, packaging mode, windowing model, theme resources, Mica/Acrylic availability, and adaptive layout.
- WPF and WinForms work should preserve designer files, resources, high-DPI behavior, threading affinity, and data-binding assumptions.
- Maintain-in-place .NET Framework UI work and modernization planning belong to `dotnet-legacy`.

## Accessibility And Localization

- Use platform semantics: MAUI `SemanticProperties`, web ARIA, WPF/WinUI automation peers, labels, focus order, keyboard navigation, and contrast.
- Keep localization in the existing resource model: `IStringLocalizer`, `.resx`, pluralization, RTL, date/number formatting, and fallback culture behavior.
- Do not hardcode UI text in new shared components when the app already localizes that surface.

## Native Platform Bindings

- Use Java.Interop/Android bindings or ObjCRuntime/Apple bindings only when .NET abstractions are insufficient and platform validation is owned.
- Keep native binding generation, metadata transforms, linker/trimming behavior, and platform assets under platform-specific validation.
