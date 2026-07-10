# Architecture

## Module Layers

The project keeps the RAG pipeline modular under `src/marketing_knowledge_agent/`.

| Module | Responsibility |
| --- | --- |
| `models` | Pydantic models, metadata schema v0.2, citations, search filters, answer payloads. |
| `ingestion` | Reads Markdown files and YAML frontmatter into validated documents. |
| `chunking` | Splits documents into chunks while preserving document id, chunk id, source path, and metadata. |
| `indexing` | Stores documents/chunks/metadata in SQLite and builds FTS5 search tables plus deterministic local embedding vectors. |
| `retrieval` | Runs keyword, vector, and hybrid search with metadata filters and non-retrievable record type protection. |
| `reranking` | Applies deterministic reranking based on keyword match, metadata signals, and freshness. |
| `generation` | Mock generator that produces answer text, citations, freshness notes, and governance warnings without external LLM calls. |
| `pipeline` | Orchestrates ingestion, search, ask, and agent ask entry points. |
| `agentic` | Agentic-lite planner / executor / reflection layer. It wraps existing retrieval and generation rather than bypassing citations or governance. |
| `evaluation` | Built-in evaluation cases for citation coverage, filter correctness, and warning coverage. |
| `excel_ingestion` | Normalizes Excel workbook rows into schema-shaped preview records. Does not write to Vault or build formal index. |
| `excel_preview` | Creates JSON / Markdown preview artifacts and governance preview stores. |
| `review_template` | Converts preview JSON into a human review decisions CSV and review summary. |
| `review_decision_validation` | Validates a filled human decision CSV against preview JSON and governance policy checks. |
| `apply_review_decisions` | Applies validated human review decisions into preview-only Vault/governance/internal inventory outputs. It does not sync Obsidian or build a formal index. |
| `governance` | Restricted customer matching and governance warning / blocking helpers. |
| `validation` | Markdown vault metadata validation. |

## CLI Commands

| Command | Purpose | Safety scope |
| --- | --- | --- |
| `mka ingest` | Build SQLite index from Markdown vault. | Uses selected vault and SQLite DB path. |
| `mka validate` | Validate Markdown vault metadata. | Read-only. |
| `mka backfill-report` | Generate metadata backfill candidates. | Review-only report. |
| `mka search` | Search indexed knowledge. | Read-only against SQLite DB. |
| `mka ask` | Generate mock RAG answer with citations. | Read-only against SQLite DB. |
| `mka agent-ask` | Run deterministic agentic-lite RAG flow. | Read-only against SQLite DB. |
| `mka evaluate` | Run prototype evaluation cases. | Uses evaluation DB path. |
| `mka excel-preview` | Create JSON / Markdown previews from Excel workbook. | Does not modify Vault or formal index. |
| `mka review-template` | Create human review decisions CSV from preview JSON. | Does not apply decisions. |
| `mka validate-review-decisions` | Validate filled review decisions CSV. | Does not apply decisions. |
| `mka apply-review-decisions` | Create preview-only Vault/governance/internal inventory outputs from validated decisions. | Writes preview only; does not sync Obsidian or build a formal index. |

## Data Flow

### Markdown RAG Flow

```text
Markdown vault
→ ingestion
→ metadata validation
→ chunking
→ SQLite documents / chunks / FTS5 / local vectors
→ retrieval
→ reranking
→ mock generation
→ answer with citations and warnings
```

### Excel Governance Flow

```text
Excel workbook
→ excel-preview
→ normalized preview JSON
→ review-template
→ human review_decision CSV
→ validate-review-decisions
→ apply-review-decisions preview
→ approved Vault preview / governance table preview
→ human confirmation
→ future Obsidian sync / formal index build
```

## Citation v0.2 Schema

Citation fields currently include:

- `label`
- `title`
- `source_path`
- `chunk_id`
- `status`
- `source_type`
- `record_type`
- `data_classification`
- `can_quote_externally`
- `publish_date`
- `updated_date`
- `captured_date`
- `last_reviewed`
- `source_sheet`
- `source_row`
- `canonical_url`
- `freshness_note`

Excel-derived citations must include `record_type`, `data_classification`, `can_quote_externally`, `source_sheet`, `source_row`, and review/freshness context when available.

## Governance Decision Flow

1. Preview records are normalized and enriched.
2. Governance risk fields are attached to records, not inferred later from summary Markdown.
3. `review-template` creates one row per record requiring human review.
4. Humans fill `review_decision`; `suggested_action` remains advisory only.
5. `validate-review-decisions` checks governance constraints and preview row coverage.
6. `apply-review-decisions` creates preview-only outputs after validation; it must not immediately sync to Obsidian or build a formal index.

## SearchFilters / Metadata Filtering

`SearchFilters` supports filters for:

- `record_type`
- `source_type`
- `content_category`
- `parent_source_type`
- `brand_name`
- `merchant_handle`
- `merchant_status`
- `product`
- `industry`
- `sales_category_lv1`
- `sales_category_lv2`
- `content_tags`
- `topic`
- `metric_type`
- `metric_name`
- `claim_status`
- `data_classification`
- `exposure_channel`
- `funnel_stage`
- `status`
- `can_quote_externally`

Future external use cases should enforce `can_quote_externally` and `allowed_exposure_channels` before generation.

## Content Index Eligibility

May enter content index after review:

- `content_asset`
- `merchant_case`
- `public_metric` only when approved and allowed channels are present

Must not enter general content index:

- `restricted_customer`
- `handle_mapping`
- `pending_metric` for external-facing retrieval
- `merchant_case` with `no_valid_content_asset=true`
- `public_metric` with missing allowed exposure channels

## General RAG Citation Eligibility

Must not appear as general RAG citations:

- `restricted_customer`
- `handle_mapping`
- `pending_metric` in external-facing responses
- Any record with `can_quote_externally=false` for external content generation

Records with governance risks may be used for internal review context only if warnings remain visible.
