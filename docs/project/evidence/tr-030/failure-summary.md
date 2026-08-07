# Failure summary

- An initial Release build found a stale Workouts view reference to a nonexistent `Schedule` property. It was corrected to use the persisted scheduled-start field; the subsequent build passed with zero warnings and errors.
- The first replay assertion expected a convenience `replayed` field that Calendar receipt replay does not add. The contract promises the original result, so the test now verifies the exact original run version and HTTP result instead.
- No treadmill command, BLE operation, long-duration test, power cycle, deployment, or external integration was used.

