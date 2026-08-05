---
name: treadmillrunner-protocol-evidence
description: Use when producing independently authored TreadmillRunner BLE protocol evidence from owner-provided captures, local observations, golden fixtures, CRC checks, or explicitly approved non-motion hardware validation; do not use to copy or translate third-party implementation code or to control treadmill motion.
---

# TreadmillRunner Protocol Evidence

## Goal

Produce reviewable, independently authored BLE protocol evidence without importing implementation code, exposing capture secrets, or causing treadmill motion.

## Workflow

1. Define the evidence question, affected characteristic or frame, expected observation, and the allowed evidence directory under `docs/project/protocol-evidence/`.
2. Create a provenance record before interpreting bytes. Record capture identifier, source type, date, device/firmware label when known, collection method, owner approval when hardware is involved, sanitization actions, applicable license or clean-room restriction, and the author of the interpretation.
3. Establish independent authorship. Use only public protocol facts, owner-provided sanitized captures, project-authored fixtures, and direct local observations. Describe observed byte positions and hypotheses in original language.
4. Sanitize captures before retention. Remove or replace device names, addresses, pairing material, account/user identifiers, timestamps that identify a person, location data, unrelated traffic, and credential-like values. Keep a deterministic redaction legend that cannot reconstruct the removed values. Do not retain raw unsanitized captures in the repository.
5. Build the smallest golden fixture that proves the hypothesis. Record exact input bytes, expected decoded fields, byte order, scaling, framing assumptions, expected CRC/checksum, and the algorithm parameters used to calculate it. Include at least one negative or corrupted-frame case.
6. Verify golden fixtures and CRC/checksum behavior with project tests or a deterministic, reviewed local command. A passing decoder alone is insufficient: compare the expected bytes or checksum explicitly and retain the command and result.
7. Escalate hardware validation by stage only when offline evidence is insufficient:
   - Stage 0: golden fixtures and recorded captures only; no device access.
   - Stage 1: owner-approved passive scan or capture; no connection or writes.
   - Stage 2: owner-approved connection, service discovery, reads, or notification subscription; no characteristic writes.
   - Stage 3: only a separately approved, project-owned non-motion command with the treadmill empty, safety key controlled by the owner, emergency stop reachable, and device state continuously observed.
8. Reconcile the observation with the hypothesis. Record supporting and contradicting bytes, firmware/device limitations, ambiguity, and the next safe test. Do not generalize one model or firmware observation into a universal protocol claim.

## Guardrails

- Never copy, port, translate, transcribe, decompile, or structurally imitate GPL or other third-party implementation code. Do not use a third-party implementation as a line-by-line oracle. If clean-room independence cannot be demonstrated, stop and request owner/legal direction.
- This passive/non-motion evidence skill never authorizes remote belt `Start`. A future Start test requires the separate TR-006B story, a dedicated motion-capable acceptance workflow, and model/firmware-specific owner approval. Until those exist, do not send Start or any unknown write that may create motion.
- Hardware access requires explicit owner approval naming the device, stage, command class, observer, and time window. Approval for one stage does not authorize the next.
- Do not browse, fetch repositories, call cloud services, install tools, upload captures, use credentials, or pair through an account. Ask the owner to provide already-authorized local evidence when needed.
- Read only project files and explicitly provided local evidence. Write only sanitized evidence beneath `docs/project/protocol-evidence/`; do not modify application code, global profiles, device settings, firmware, or external systems.
- Never persist BLE addresses, pairing keys, authentication tokens, personal telemetry, or credential values. Report field presence or a non-reversible placeholder when it is material.
- Keep hypothesis, observation, and conclusion distinct. Label inferred fields and confidence; a plausible decode is not a verified protocol fact.

## Validation

- Confirm every retained capture has a sanitization record and provenance record.
- Confirm every asserted frame/field has a golden fixture or a documented reason it cannot yet have one.
- Run the relevant project test or deterministic fixture/CRC command and retain exact command, exit status, expected value, actual value, and evidence path.
- For hardware stages, retain owner approval, preflight checklist, observed device state, commands/actions performed, timestamps, stop condition, and result. A skipped hardware stage is acceptable when offline evidence answers the question.
- Reject completion when evidence relies on unsanitized data, GPL-derived implementation detail, an unexplained checksum, stale test output, or an unsupported device/firmware generalization.

## Extension Points

Workflows may supply a bounded evidence question, approved project evidence path, fixture/test commands, and a hardware approval record. Outputs are sanitized provenance Markdown or JSON, golden fixture references, deterministic validation results, and explicit unsupported claims. Workflow wrappers may orchestrate project-owned checks but may not add network access, credentials, installs, external writes, motion control, or broader evidence paths.

## Completion Contract

Report the evidence question, sources and provenance, independent-authorship basis, sanitization performed, fixture paths, CRC/checksum parameters and expected/actual results, validation commands and checks, hardware stage reached or skipped, owner approval reference, unsupported claims, contradictions, stop conditions encountered, skipped, blocked, and failed checks, and the next safe action. Optional setup or install behavior is always reported as skipped or failed; record whether it is safe to continue as a non-blocking path when offline deterministic evidence is sufficient. This skill never performs setup or installs tools.

Report `Skill used: treadmillrunner-protocol-evidence - <reason>` when this skill materially affects the work.

## Stop Rules

- Stop before retaining raw or insufficiently sanitized captures.
- Stop if a requested interpretation depends on copying, translating, or closely following GPL or other third-party implementation code.
- Stop before any hardware command without explicit stage-specific owner approval.
- Stop immediately if the belt moves, device state becomes uncertain, the safety observer is unavailable, the approved window ends, or a command response differs from the expected non-motion response.
- Stop rather than send remote belt `Start` under this skill, an unknown characteristic write, or any command that may cause motion. Hand a Start question to TR-006B instead of treating passive evidence approval as authority.
- When offline evidence is incomplete and safe approved hardware evidence is unavailable, mark the claim unsupported and hand off the exact missing evidence request.
