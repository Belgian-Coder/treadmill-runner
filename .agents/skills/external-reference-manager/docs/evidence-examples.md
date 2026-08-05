# External Reference Evidence Examples

## Credential Boundary

- Keep Git credentials and PATs out of reference manifests.
- For Azure DevOps or private Git remotes, rely on the local credential manager or environment-supported authentication.
- Dry-run reports must show clone, fetch, pin, and card intent before writing.

## Deterministic Packet

```shell
python -B .agents/skills/external-reference-manager/scripts/sync_references.py --manifest automations/reference-refresh/artifacts/references/reference-manifest.json --output-root automations/reference-refresh/artifacts/references --dry-run --no-fetch --format json
```

The workflow wrapper supplies the same output folder automatically:

```shell
python -B .agents/manage.py reference-refresh --mode dry-run --no-fetch --format json
```

Expected evidence includes pinned commit, remote URL without inline credentials, stale-pin warnings, divergence summary, card freshness metadata, license/notice facts when available, and skipped credential checks when the remote cannot be reached safely.
