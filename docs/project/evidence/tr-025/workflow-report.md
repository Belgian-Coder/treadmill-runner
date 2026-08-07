# Workflow report — TR-025

The repository `user-story-workflow` was started as `US-TR-025`; its plan check passed before implementation and the owner's instruction to proceed supplied the approval gate.

## Completed work packages

1. Added the internal plan-workout classification and hid those definitions from ordinary workout surfaces.
2. Added durable profile-owned run schedules and deterministic ordered calendar projection without creating hundreds of editable calendar rows.
3. Added runner context, add/schedule/keep-later/start UX, exact program selection on Run, profile switching, and responsive calendar cards.
4. Added reviewed migration/model changes, unit/integration/browser coverage, current documentation, and sanitized showcase evidence.

## Acceptance disposition

All TR-025 acceptance criteria pass. The implementation never prepares a treadmill session or sends a treadmill command. Newly generated data follows the new contract; compatibility for disposable pre-release test copies was intentionally omitted by owner decision.

The full browser sweep found two adjacent regressions: accumulated long runner names could overflow an iPhone calendar and a gallery test expected the old generic Cancel label. Both were corrected. The final targeted matrix passed 15 of 15 desktop, tablet, iPhone, gallery, and TR-025 scenarios.
