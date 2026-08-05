# Mermaid Doctor Evidence Packet

Use `validate_mermaid.py --doctor` when a workflow needs deterministic Mermaid evidence without applying fixes or installing tools.

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --doctor --format json --non-blocking
```

## Guarantees

- Does not write Markdown files.
- Does not run `--fix`.
- Does not install Mermaid CLI.
- Does not install VS Code extensions.
- Uses temporary files only for optional local render probing.
- May inspect user-level VS Code extension state; do not run under strict no-profile dogfood unless that inspection is allowed or source-reviewed for the current environment.

## Stable JSON Fields

- `schema_version`: doctor packet schema version.
- `tool`: `mermaid-diagrams-azure-devops.doctor`.
- `ok`: false only when static validation or render execution has hard failures.
- `status`: stable `overall`, `parser`, `wrapper`, `render`, and `setup` statuses.
- `write_policy`: read-only and install-disabled flags.
- `files_scanned`: Markdown and `.mmd` files included in the evidence packet.
- `block_count`: Mermaid block count.
- `diagram_types`: count by detected Mermaid diagram type.
- `wrappers`: count by wrapper type such as `source`, `azure`, or `fenced`.
- `static_validation`: parser/static validity, counts, errors, and parser warnings.
- `azure_wrapper`: Azure wrapper warning status and findings.
- `render`: render availability, command, failure count, warning count, and render findings.
- `setup`: VS Code preview setup detection status without extension installation.

## Status Values

- `pass`: no errors or warnings for the area.
- `warn`: evidence was collected but non-blocking warnings exist.
- `fail`: hard errors exist.
- `skipped`: the area was not applicable or could not be checked without setup.

When `--non-blocking` is used, the shell exit code can still be `0`; use the JSON `ok`, `status`, `static_validation`, `render`, and `setup` fields as the evidence.
