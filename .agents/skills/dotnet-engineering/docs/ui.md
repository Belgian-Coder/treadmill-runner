# UI

Use for modern .NET UI after confirming target framework, UI stack, platforms, workloads, and existing design/test conventions.

## Framework Selection

- Browser-only: use Blazor when SSR, interactive Server, Auto, or WebAssembly trade-offs match the page. Static SSR fits content/forms; WebAssembly fits offline/static hosting with download, trimming, and AOT cost.
- Mobile-first iOS/Android: use MAUI when native controls, Shell, single-project layout, or Xamarin.Forms migration matter.
- Broad platform reach: consider Uno Platform or Avalonia only after checking support model, tooling, controls, packaging, and team familiarity.
- Windows-only: prefer WinUI 3 for new Fluent Windows apps, modern WPF for mature XAML ecosystems, and WinForms for internal tools/simple forms.
- Maintain-in-place .NET Framework WPF/WinForms, old project files, and installer constraints belong to `dotnet-legacy` unless migration is explicit.

## Blazor

- Pick render mode per page/component; do not make every page interactive by default.
- Keep browser-run components in the client project for WebAssembly or Auto modes.
- Use `PersistentComponentState` for prerendered data that must survive hydration.
- Call JS interop from `OnAfterRenderAsync(firstRender: true)` when DOM elements are required.
- Keep Static SSR form identity explicit.
- Use source-generated serialization and trim-safe patterns when WebAssembly AOT or trimming is enabled.
- Treat enhanced navigation and streaming rendering as server-rendering behavior, not proof of interactivity.
- Keep state ownership local: parameters down, events up, and broad cascading values rare.

## MAUI

- Preserve single-project layout; platform code stays under `Platforms/`.
- Prefer MAUI abstractions such as `DeviceInfo.Platform`; trigger async work from lifecycle events or commands, not constructors.
- Use compiled bindings with `x:DataType` where practical.
- Follow existing `CommunityToolkit.Mvvm`, Shell, page lifetime, DI, and platform-service conventions.
- Treat Native AOT on iOS or Mac Catalyst as a deployment choice needing trimming and library evidence.
- Keep pages transient unless retention is intentional; persist state in view models/services.
- Do not install workloads or platform SDKs automatically; report missing workloads as skipped or blocked validation.

## Desktop And Cross-Platform

- Uno: inspect target frameworks, `UnoFeatures`, Extensions modules, theme/toolkit packages, and active targets; use MVUX only when accepted by the project.
- Avalonia: verify maturity, controls, packaging, and team familiarity before choosing it over MAUI, Uno, WinUI, or WPF.
- WinUI 3 uses `Microsoft.UI.Xaml`; do not assume UWP APIs like `Window.Current` or `CoreDispatcher`.
- WPF can use host-builder, DI, MVVM Toolkit, and modern C# while preserving designer/resource constraints.
- WinForms is acceptable for internal tools; high-DPI, dark mode, designer files, and event wiring are regression risks.

## Accessibility, Localization, Review

- Icon-only Blazor buttons need accessible labels; routine status updates use polite live regions.
- Verify contrast, keyboard navigation, focus order, high-contrast themes, and screen-reader behavior after UI changes.
- Prefer `.resx` or existing localization; treat RTL, pluralization, and culture formatting as behavior.
- Ensure the platform/deployment model justifies the framework, prerequisites are reported, and tests or manual validation cover changed UI behavior.
