# Runtime Triage

Use this file to choose the first evidence source before opening debugger tools.

## Symptom Map

- Crash: start with exception type, process exit code, crash report, event log, container restart reason, and available dump.
- Hang or deadlock: capture thread stacks or use existing dumps; look for stable waits, blocked message pumps, locks, waits, thread pool starvation, or external dependencies.
- High CPU: correlate runtime counters, OS thread CPU, traces, flame graph width, and repeated stacks; do not infer hot code from one stack unless the sample is repeated or supported by trace data.
- Memory pressure: compare heap size over time, GC counters, dump heap summaries, large object heap usage, pinned objects, finalizer queues, and retention roots.
- Thread pool starvation: look for sustained queue length, slow injection, sync-over-async blocking, long-running pool work, and correlated latency spikes.
- Startup failure: inspect host logs, config binding, missing assemblies, native dependencies, IIS/app pool events, container logs, and environment differences.
- Unknown runtime failure: preserve already available artifacts first. If more data is needed, define the snapshot type, process, output path, sensitivity, and approval before collection.

## Evidence Order

1. Existing logs, crash reports, CI artifacts, and dumps.
2. Existing counters, traces, or monitoring evidence.
3. New low-impact counters or logs only after approval, because they still observe live processes or environments.
4. New traces or dumps only after approval.
5. Live attach only when dump/trace evidence is insufficient and risk is accepted.

## Confidence

Use low confidence for a single stack, medium confidence for repeated stacks, flame graph concentration, or correlated counters, and high confidence only when stack/heap/trace evidence points to the same cause and alternative explanations were checked.
