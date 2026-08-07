# Sanitized failure summary — TR-027

- The first deterministic validation found NuGet lock files that had been evaluated under inconsistent normal/publish runtime properties. The project bootstrap regenerated them; locked restore then passed.
- Formatting verification found an unformatted generated migration and inconsistent line endings inherited from a restored prior migration. Repository formatting normalized them without a content change to the prior migration.
- One existing training-plan browser assertion used obsolete progress wording. It was updated to the current visible contract.
- The first complete browser invocation exceeded the command cap because older tests still waited for removed page-level runner radios. Those tests were converted to the global runner selector and all 57 browser cases then passed in bounded groups.
- The first two-runner switch did not render before the forced refresh. The selector now updates visibly, persists the exact profile ID, and then refreshes; its two-profile isolation test passes.
- Gallery preparation assumed that choosing a runner also chose a workout. It now makes both choices explicitly, matching the product flow.

No production data was deleted, no treadmill or Bluetooth command was sent, and no credential or device identifier was captured.
