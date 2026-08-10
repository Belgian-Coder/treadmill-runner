# Sanitized failure summary

- The first responsive Run assertion used the old two-button expectation. It was updated to the approved single **Prepare run** action.
- The first Control pass could not see seeded workouts because the test did not open the new collapsed **Other workout** section. Shared browser helpers now exercise the intended interaction.
- Focus buttons initially emitted minimized boolean ARIA values. They now emit explicit `true` and `false` strings.
- On iPhone Chart focus intentionally covers the surrounding dashboard; the test now exits through the visible **Collapse live graph** control before checking Balanced mode.

No hardware or treadmill command was used while diagnosing these failures.
