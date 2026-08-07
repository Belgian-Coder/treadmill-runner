# Accepted plan — TR-025

Make premade plans genuinely runner-owned. Workouts must expose the active runner, catalog receipts and installed plans must reload when the runner changes, and a runner must not see or start another runner's plan. Workouts generated as plan internals stay out of the shared library and manual selectors.

After adding a plan, offer a reversible choice: schedule it now or keep it inactive for later. Scheduling records a first training date that is also a selected training day, the template's required number of weekdays, and an ordered immutable program run. Derived calendar entries show plan, position, total, phase, and week and retain exact run/item identity so only the intended completed item advances progress.

The owner confirmed that existing data was test-only, so no pre-release compatibility conversion is required. Do not deploy, publish a release, tag GitHub, send treadmill commands, commission BLE, or run long-duration/power-cycle acceptance.
