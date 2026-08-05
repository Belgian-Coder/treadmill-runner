# Dump And Trace

Use this file for diagnostic tool choice and safety boundaries.

## Tool Choice

- `dotnet-dump`: collect or analyze managed dumps when source and runtime evidence are insufficient.
- `dotnet-trace`: collect EventPipe traces for CPU, runtime, or request timing investigations.
- `dotnet-counters`: observe live runtime counters before heavier collection; this still requires process access approval.
- `dotnet-gcdump`: collect lighter managed heap evidence when a full dump is too large.
- `WinDbg`: inspect Windows dumps, native/managed stacks, wait chains, heap data, and symbols.
- `LLDB/SOS`: inspect Linux or macOS dumps when `dotnet-dump` is not enough or native frames matter.
- `createdump`: configure or trigger runtime dump collection on Linux; configuration changes are diagnostic mutations and need approval.
- `dotnet-monitor`: collect production-friendly dumps, traces, logs, or metrics only when the endpoint and auth model are approved.

## Safety

- Datasets may contain secrets, personal data, connection strings, tokens, request bodies, and business data.
- Full dumps can be large and can stress disk or memory; container dumps may trigger OOM if limits are tight.
- Live attach can pause or perturb the target process.
- Symbol loading can require network access; record when network is unavailable or not approved.
- Container diagnostics may require PID namespace access, ptrace capability, or elevated permissions.
- Production artifacts, even when already collected, can contain sensitive data and need an approved handling boundary before inspection.

## Minimum Capture Notes

- Record process name, PID, OS, architecture, runtime version, container or host identity, dump type, collection time, and tool version when known.
- For hangs, prefer two captures separated by enough time to confirm stable waits.
- For high CPU, correlate hot OS threads with managed thread IDs or trace samples.
- For memory, record whether evidence is full dump, heap dump, GC dump, or counters only.
