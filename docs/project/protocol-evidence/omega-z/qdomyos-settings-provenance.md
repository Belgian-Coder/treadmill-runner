---
title: QDomyos Omega Z settings provenance
type: protocol-evidence
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# QDomyos Omega Z settings provenance

## Evidence question

Which QDomyos settings have been publicly reported to make a Horizon Omega Z
work, and which safe defaults can TreadmillRunner preserve before the exact
machine is available?

## Sources and collection

- Source type: public GitHub issues, comments, pull request, and commit.
- Collection date: 2026-08-02.
- Collection method: user-requested GitHub review plus a read-only search of the
  sibling QDomyos checkout pinned at
  `99f27b2cb5360ce925c19c40d5a4ddff29ef4057`.
- Device label reported upstream: Horizon Omega Z; firmware was not reported.
- Interpretation author: TreadmillRunner project agent.
- License boundary: QDomyos-Zwift is GPL-3.0. No implementation code, attached
  debug logs, packet captures, or command bytes were copied into this record.

## Sanitization

No attachments or raw logs were downloaded or retained. Public issue text was
reduced to setting names, outcomes, dates, and links. Email addresses, device
identifiers, notification tokens, and unrelated quoted email content were not
stored.

## Observations

1. [Issue 3137](https://github.com/cagnulein/qdomyos-zwift/issues/3137)
   reported a connected Omega Z with no speed or metrics. The maintainer's
   [Force FTMS recommendation](https://github.com/cagnulein/qdomyos-zwift/issues/3137#issuecomment-2629857424)
   was followed by the user's
   [confirmation that it worked](https://github.com/cagnulein/qdomyos-zwift/issues/3137#issuecomment-2642476993).
2. [Issue 3809](https://github.com/cagnulein/qdomyos-zwift/issues/3809)
   contains a later
   [user confirmation](https://github.com/cagnulein/qdomyos-zwift/issues/3809#issuecomment-4638764830)
   that enabling `Paragon X` and `Horizon 7.8 start issue` made Omega Z
   speed/incline behavior work.
3. [Pull request 4698](https://github.com/cagnulein/qdomyos-zwift/pull/4698)
   states that the new `Omega Z` toggle applies those two behaviors. The merged
   [commit 78256d9](https://github.com/cagnulein/qdomyos-zwift/commit/78256d96c)
   confirms the alias; it does not include `Force FTMS` in that alias.
4. [Issue 841](https://github.com/cagnulein/qdomyos-zwift/issues/841#issuecomment-1177228425)
   says the Omega uses FTMS for inclination, which conflicts with treating the
   vendor control candidate as universally correct.

## Project conclusion

- Prefer FTMS for initial read-only telemetry.
- Preserve the Paragon-compatible behavior as a named control candidate.
- Preserve the 7.8 startup workaround as the safer, project-owned rule that a
  session enters Running only after sustained measured belt movement.
- Keep every outbound capability disabled until project-owned captures and
  stage-specific hardware tests verify the exact Omega Z.
- Do not silently switch protocols or combine FTMS and vendor writes during a
  session.

These defaults are represented by `OmegaZCompatibilityProfile.Default`. The
generic `ITreadmillProtocol` and `TreadmillProtocolRegistry` boundaries allow a
future Domyos adapter to supply different matching, protocol choices, and
capabilities without changing the session engine.

## Validation and limits

- Hardware stage reached: Stage 0, public evidence and local source metadata
  only; no device access and no BLE writes.
- Golden byte fixture: not applicable to this setting-to-profile mapping. The
  existing byte-level protocol fixtures remain separate and do not validate
  these runtime choices.
- Unsupported claim: the exact treadmill's safest control transport remains
  unknown until arrival-day read-only evidence and later approved control gates.
- Next safe action: detect `JFTMOmega Z`, enumerate `1826` and `FFF0`, and compare
  passive FTMS/vendor telemetry while controls remain disabled.
