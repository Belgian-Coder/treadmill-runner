# Framework Patterns

Use this file when the system uses framework-era APIs or host contracts that should be maintained rather than silently replaced with modern .NET patterns.

## Data Access

- Treat ADO.NET, typed DataSets, DataTables, stored procedures, EF6, NHibernate, and hand-written transaction scopes as compatibility surfaces.
- Preserve connection-string names, provider names, ambient transactions, command timeouts, and exception handling behavior unless the defect is there.
- Do not switch EF6 to EF Core, replace DataSets with new DTO models, or introduce a new repository layer as part of an unrelated fix.

## Classic ASP.NET State And Auth

- Web Forms ViewState, PostBack, EventValidation, control IDs, Session, Application state, and Global.asax startup order are behavior.
- Classic ASP.NET MVC route tables, filters, model binding, anti-forgery setup, Forms Authentication, Membership/Roles/Profile, and OWIN/Katana startup should be changed narrowly.
- Do not replace Membership with ASP.NET Core Identity or rewrite request startup unless modernization is approved.

## Remoting, Isolation, And Service Boundaries

- WCF contracts, bindings, endpoint names, behaviors, `.svc` files, generated clients, and service references must stay consistent with config.
- AppDomain isolation, .NET Remoting, CAS remnants, shadow copying, plugins, and reflection-based loading often exist for deployment or tenant isolation reasons.
- Prefer compatibility wrappers around risky edges; do not collapse service boundaries as a cleanup step.

## Threading And Runtime Assumptions

- Preserve caller expectations around synchronous APIs, ASP.NET request context, `SynchronizationContext`, WinForms `Control.Invoke`, WPF `Dispatcher`, BackgroundWorker, timers, and `ThreadPool.QueueUserWorkItem`.
- Avoid broad async rewrites in legacy request, UI, or service paths unless tests and runtime evidence cover threading and shutdown behavior.
- Check shutdown, cancellation, exception logging, and finalizer/dispose behavior before changing long-running loops or background work.

## Interop And Deployment

- COM registration, P/Invoke, native DLL probing, x86/x64 bitness, registry keys, GAC assemblies, install paths, service accounts, and certificate stores are runtime inputs.
- Keep installer, IIS, Windows service, and desktop deployment assumptions visible in the handoff.
