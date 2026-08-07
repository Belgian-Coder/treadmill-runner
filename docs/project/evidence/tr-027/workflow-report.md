# Workflow report — TR-027

- Workflow: `user-story-workflow`
- Result: implemented and validated
- Plan quality: 194 passed, 0 failed
- Release build: 0 warnings, 0 errors
- Persistence: additive reviewed migration; fresh migration, constraints, and integrity checks passed
- Browser: 57 cases passed in bounded groups against a freshly published host
- Installation: locally signed 1.5.18 activated; service and retained planning data verified
- Safety: no treadmill movement, BLE commissioning, remote Start, update publication, or GitHub action occurred

The implementation keeps generated premade sessions inside their owner profile's training plan, retains custom reusable workouts in the library, and uses durable template-program provenance for legacy compatibility classification. The header runner selector is immediately visible, browser-local, and refreshes profile-scoped data after a switch.

Calendar mutations require profile ownership, an active plan run, expected run version, operation ID, and preview. A full plan remains a logical projection rather than hundreds of persisted calendar rows. Extra repeats are calendar attempts and do not rewind or double-advance progression.
