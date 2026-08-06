# Accepted plan — TR-023

## Outcome

Make active sessions gateway-owned through browser interruptions, preserve
truthful elapsed time across BLE gaps, reconcile only a verified moving session
with fresh generation-bound commands, and recover tracking after a gateway
restart without issuing any treadmill command until the runner explicitly
resumes automatic speed and incline control.

## Safety invariants

- Recovery never encodes or issues Start.
- An uncertain command is never retried or replayed.
- A connection-generation change expires earlier intents.
- A physical-console change outside one verified increment prevents automatic
  reconciliation.
- Browser loss removes manual authority but does not claim the treadmill stopped.
- A service restart restores tracking only; commands remain suspended until an
  explicit, lease-bound, version-bound resume operation succeeds.

## Accepted scope

The owner explicitly approved the combined TR-023/TR-024 implementation plan on
2026-08-06. TR-023 was executed first through `user-story-workflow`. No live
treadmill commands, deployment, release, soak run, or power-cycle test was
authorized for this story.
