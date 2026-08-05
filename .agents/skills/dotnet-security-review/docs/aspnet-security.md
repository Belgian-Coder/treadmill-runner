# ASP.NET Security

Use this file when reviewing ASP.NET Core APIs, web apps, workers, or config changes that affect authentication, authorization, browser security, secret handling, cryptography, or package trust.

## Identity And Tokens

- Identify the authentication model before reviewing code: ASP.NET Core Identity, external OIDC provider, JWT bearer API, cookies, passkeys, or a mixed scheme.
- For Identity, check password, lockout, email-confirmation, MFA/passkey, recovery-token, and rate-limit posture. Treat public login, register, refresh, and reset endpoints as brute-force targets.
- For OIDC, review authority, client secret storage, callback URLs, scopes, claim mapping, and `MapInboundClaims`. Broken claim mapping can make role/name checks silently fail.
- For JWT, review issuer, audience, signing key source, lifetime, refresh-token storage, and `ClockSkew`. Zero skew can cause operational false negatives; broad skew weakens expiry.
- Do not infer authorization from authentication. Check fallback policies, endpoint attributes, route groups, resource ownership, tenant checks, and IDOR exposure.

## Browser And Middleware Security

- Review CORS for explicit origins, methods, headers, credentials, and environment-specific policies. Treat wildcard-origin behavior with credentials as high risk.
- Review CSRF posture for cookie-authenticated state-changing requests. APIs using bearer tokens have different CSRF risk than browser cookies.
- Check cookie settings such as `CookieSecurePolicy`, `SameSite`, `HttpOnly`, forwarded headers behind TLS-terminating proxies, and session fixation controls.
- Check middleware order: CORS before authorization, authentication before authorization, rate limiting before protected endpoint execution, and exception handling outermost.
- For browser-rendered content, review Content Security Policy, X-Content-Type-Options, frame-ancestors/clickjacking, and raw HTML rendering.

## Secrets And Cryptography

- Review secret sources across local dev, CI, staging, and production. User secrets are development-only; production secrets should come from environment, managed identity, vault, or the platform's secret store.
- Redact secret values in findings. Report key names, file paths, and kind; do not paste tokens, private keys, connection strings, or personal data.
- Prefer ASP.NET Core Data Protection for web-app token/cookie protection and key rotation. Review key persistence, application name, deployment sharing, and key lifetime when multiple instances are involved.
- Flag hardcoded signing keys, reused AES-GCM nonces, weak hashes, custom password hashing without clear parameters, and deprecated algorithms.

## Package And SSRF Review

- Check NuGet audit settings, especially `NuGetAuditMode`, source mapping, HTTP feeds, private-feed credentials, unsigned packages, and package-source confusion risk.
- For outbound HTTP from user-controlled URLs, review SSRF defenses: allowlists, scheme restrictions, private-address blocking, redirects, DNS rebinding, timeout limits, and post-resolution validation.
- Treat raw request-body logging, auth-header logging, and verbose production exception pages as security findings even when they do not expose secrets in the current diff.

## Review Checklist

- Auth scheme, authorization model, and resource ownership checks are explicit.
- Browser-facing endpoints have CORS, CSRF, cookie, headers, and middleware order reviewed.
- Secrets and crypto findings are redacted and tied to local evidence.
- Package-source trust and SSRF-capable HTTP paths are covered or explicitly skipped with reasons.
