# UI And Hosting

Use this file for classic ASP.NET, WCF, WPF, WinForms, Windows services, and hosted desktop/server constraints.

## Classic ASP.NET And WCF

- Web Forms lifecycle, ViewState, custom HttpModules, HttpHandlers, Global.asax, and web.config settings are behavior.
- Classic ASP.NET MVC route tables, filters, model binding, and authentication modules may depend on startup order.
- WCF endpoints, bindings, behaviors, generated clients, and config-driven service references must stay in sync.
- IIS app pool identity, bitness, pipeline mode, virtual directories, and machine-level config can affect runtime behavior.

## WPF And WinForms

- Preserve designer ownership: generated code, `.resx`, control names, event wiring, resources, and XAML names.
- Keep UI-thread access explicit; do not move blocking work or async continuations without checking dispatcher/invoke behavior.
- Treat high-DPI, anchoring, scaling, fonts, localization, and accessibility as visible regression surfaces.
- For migration or runtime upgrade of WPF/WinForms, use `dotnet-legacy`.

## Windows Services And Installers

- Service account, recovery options, event log source, working directory, config file path, and shutdown behavior matter.
- Setup projects, WiX, ClickOnce, MSIX, and custom installers are deployment behavior, not packaging trivia.
- Avoid changing service names, registry keys, install paths, or certificate store assumptions without explicit scope.

## Review Checklist

- Host lifecycle and startup order remain stable.
- Generated UI artifacts are intentionally changed or untouched.
- Installer/service behavior is validated or clearly blocked.
- Runtime platform assumptions are reported in the handoff.
