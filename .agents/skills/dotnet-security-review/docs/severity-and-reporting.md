# Severity And Reporting

Use this file to produce compact, defensible findings.

## Severity

- Critical: unauthenticated remote code execution, exploitable injection, data breach path, unsafe deserialization with attacker-controlled input, or hardcoded live secret.
- High: missing authorization, IDOR, credential exposure with plausible misuse, weak password storage, dangerous cryptography, or insecure package/source trust.
- Medium: defense-in-depth gaps such as missing security headers, verbose errors, weak lockout/rate-limit posture, permissive CORS with limited exposure, or incomplete audit logging.
- Low: best-practice deviations with low exploitability or context-dependent risk.
- Informational: observations, missing evidence, upcoming deprecations, or hardening ideas.

## Report Shape

- Finding: concise title.
- Severity: critical, high, medium, low, or informational.
- Category: OWASP, CWE, secret exposure, auth/authz, cryptography, dependency, or platform trust.
- Evidence: file path and line when available; redact sensitive values.
- Impact: what could go wrong in this project context.
- Remediation: concrete fix direction and owner.
- Confidence: high, medium, or low based on evidence quality.

## Redaction

- Replace secret values with type and suffix hints only when needed, such as `connection string ending ...abcd`.
- Do not include raw request bodies, tokens, private keys, personal data, or proprietary payload snippets.
- If exact evidence cannot be shown safely, report the file and key name plus the redaction reason.

## Handoff

- Use `dotnet-security-review` JSON or Markdown output as attached evidence when scanner findings contributed.
- Send code changes to `dotnet-engineering` or `dotnet-legacy` after the review; keep this skill focused on review and risk classification.
