# X. Slack Output Renderer Preview

## 1. Scope

`mka preview-slack-output` is an offline, read-only format preview. It consumes the existing external-intent `StructuredRetrievalResult`, validated citations, and approved Asset Apply Preview URL rows. It neither imports nor replaces the production Slack renderer.

The command has two modes:

```text
preview-slack-output --query QUERY --variant concise|standard|detailed
preview-slack-output --sample-set --output reports/slack_output_preview
```

It does not read Slack tokens, call Slack, Apply URL decisions, write Vault, rebuild SQLite, modify Excel/decisions, or enable query constraints.

## 2. Overlay Contract

The in-memory join key is `(record_id, asset_id, field)`. Only `asset_url` and `canonical_url` rows satisfying all of these gates are accepted:

- `eligibility=ready_for_apply_preview`;
- `review_decision=approve`;
- `governance_status=eligible`;
- action is add, update or no_change;
- exact matching human decision exists;
- URL is a safe absolute HTTP(S) value;
- asset does not appear in the governance-blocked set.

`asset_url` is the preview display link. `canonical_url` remains backend citation metadata and is deliberately excluded from rendered text and the user-safe JSON payload. Missing overlay identity never falls back to record-level canonical URL and produces a warning.

## 3. Shared Result Contract

One user-safe payload is built per query before formatting. Concise, standard and detailed renderers receive the same entities, assets, citations, constraints and governance outcome. They cannot retrieve, filter or add results.

The payload permits brand, handle, categories, asset type/title, approved display URL, external-usage label, simplified source, totals and applied-condition labels. It excludes internal IDs, canonical URL, provenance, confidence, file paths, metadata JSON, raw query plan and retrieval/embedding scores.

Assets without an external-safe citation or present in the blocked set are omitted. Unsupported constraints, unresolved exact lookup and zero AND intersection remain distinct abstention kinds with zero assets and citations.

## 4. Display Policy

- first response: at most 5 brand groups and 10 assets;
- always state actual totals and displayed totals;
- never change constraints, switch AND to OR, or pad results;
- preserve recognizable title context with a 160-character soft limit;
- show missing links explicitly rather than substituting record-level URLs;
- deduplicate simplified source records;
- do not show publication/interview/review status or dates without authoritative asset-level data.

Variant B (standard) is the production candidate after human format selection and a separate Asset Metadata Apply Sprint. Thread continuation is a later explicit feature, not part of this preview.

## 5. Outputs

```text
slack_output_preview_summary.md
slack_output_variant_a_concise.md
slack_output_variant_b_standard.md
slack_output_variant_c_detailed.md
slack_output_comparison.md
slack_output_contract.md
slack_output_payload_preview.json
slack_output_preview_errors.csv
slack_output_preview_warnings.csv
recommended_production_format.md
```

Reports may contain company-derived public content and stay outside version control under `reports/`.

## 6. Activation Boundary

Format approval does not Apply asset metadata or change production Slack. A later sprint must first Apply the approved asset schema through plan/confirm/execute, rebuild a temporary index, rerun governance/citation assertions, and explicitly activate the chosen renderer. `published_at`, `publication_status`, interview fields, review status and partner name remain fail closed.
