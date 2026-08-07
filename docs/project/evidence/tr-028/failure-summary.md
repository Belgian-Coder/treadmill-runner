# Sanitized failure summary — TR-028

- The first browser attempt stopped before Chromium because stale runtime-specific NuGet lock graphs prevented the temporary E2E database migration. The lock graphs were normalized and the rerun passed.
- An initial browser assertion counted total WebSocket lifetimes and therefore treated a legitimate replacement connection as duplication. The corrected test measures maximum concurrent live sockets, matching the one-connection invariant.
- No treadmill or BLE command was issued.
