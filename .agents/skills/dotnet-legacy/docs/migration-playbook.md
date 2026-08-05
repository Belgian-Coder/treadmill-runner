# Migration Playbook

Use this file for approved modernization or migration planning.

## Safe Order

1. Stabilize build and tests in the current runtime.
2. Inventory dependencies, hosting, config, and deployment.
3. Extract behavior behind interfaces where tests can prove compatibility.
4. Move leaf libraries before hosts.
5. Convert package management only when restore and binding redirects are understood.
6. Convert to SDK-style only when build customizations and generated files are mapped.
7. Change target frameworks last, with explicit compatibility evidence.

## Packages And Projects

- Treat `packages.config` to PackageReference as a behavior change because asset flow and binding redirects can change.
- Keep central package management decisions repo-wide, not per isolated file.
- Preserve custom targets, pre/post-build events, embedded resources, generated clients, and designer metadata.
- Validate NuGet restore paths and locked dependency versions before updating packages.

## Web And Services

- Classic ASP.NET MVC can often be strangled route by route behind a proxy or module boundary.
- Web Forms migration is usually a rewrite of UI flow, not a mechanical project conversion.
- WCF server migration needs protocol, binding, auth, serializer, and client compatibility decisions.
- Windows services need service account, recovery, event log, install, and shutdown behavior preserved.

## Compatibility Strategy

- Prefer side-by-side services, adapters, and shared contracts over one large conversion.
- Keep data schema, message contracts, and public APIs backward compatible until consumers move.
- Add baseline tests for serialization, config binding, URL routes, and service operations.

## Exit Criteria

- Modernized slice has deterministic build and tests.
- Runtime, package mode, hosting, and deployment changes are documented.
- Rollback or side-by-side operation is clear.
- Remaining legacy constraints are listed instead of hidden.
