# Review Scope

Use this file to select review categories without loading broad security material.

## Configuration And Secrets

- Inspect `appsettings*.json`, user-secret references, environment defaults, Dockerfiles, pipeline files, package sources, and config transforms for secret-like values.
- Report secret exposure by location and kind, but redact the value.
- Check whether local development secrets, CI secrets, and hosted-environment secrets use distinct stores.

## Auth And Authorization

- Verify authentication scheme, cookie/JWT settings, fallback policy, `[Authorize]` coverage, anonymous endpoints, and per-resource authorization checks.
- Treat missing authorization on state-changing or tenant/user-scoped endpoints as high risk until context proves otherwise.
- Distinguish authentication success from object-level authorization; authenticated users can still exploit IDOR-style gaps.

## Injection And Unsafe Inputs

- Check SQL/raw query construction, command execution, path handling, SSRF-capable HTTP clients, raw HTML rendering, model binding over-posting, and request size limits.
- Prefer parameterized queries, allowlists, output encoding, safe redirect rules, and bounded payload handling.

## Cryptography And Deprecated Surfaces

- Flag MD5, SHA1, DES, RC2, ECB mode, small RSA keys, hardcoded keys, weak password hashing, and reused AES-GCM nonces when evidence exists.
- Check for `BinaryFormatter`, unsafe deserialization, .NET Remoting, CAS attributes, `[AllowPartiallyTrustedCallers]`, and unsafe serializer project switches.

## Package And Source Trust

- Review NuGet audit policy, package source mapping, unsigned or HTTP package sources, hardcoded feed credentials, and package validation for libraries.
- Treat security scanner output as supporting evidence, not as proof that dependency risk is fully handled.
