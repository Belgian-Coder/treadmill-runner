# Accepted plan — TR-024

Implement a versioned, read-only catalog of 16 neutral premade plans under Workouts. Preview every plan for the selected runner, resolve heart-rate zone references from that profile, revalidate targets against runner and verified treadmill limits, and materialize only after an explicit action.

Materialization must be profile-scoped, idempotent, inactive, immutable, provenance-preserving, and bounded. The 58-week plan must retain 174 scheduled positions while deduplicating identical definitions and rendering by phase and week. Generic standalone QDomyos XML remains conservative; only an explicit v4 bundle with positive HR data and bounded speeds can produce adaptive HR directives.

Use read-only owner-provided examples as provenance without copying personal data. Commit sanitized deterministic evidence and populated showcase screenshots. Do not deploy, publish a release, tag GitHub, send treadmill commands, run a long soak, or perform repeated power cycles.
