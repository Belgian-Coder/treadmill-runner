# Workflow Manager Fixtures

`runtime-observation-v1.json` is a provider-neutral host-input example for the strict
runtime-observation contract. It keeps host surface, model provider, model identity,
optional deliberation, and attested capabilities separate. Tests copy it into a matching temporary run's
`validation/` directory before ingestion; the loader injects the canonical
`evidence_path` from that location.
