# Unsupported Constraint Fail-Closed Acceptance

## Scope

本報告驗證 `98700af` 之後的 Unsupported Constraint Fail-Closed & Asset Status Truth 修正。正式 108-document index 僅唯讀使用；未重建 index、未修改 Obsidian managed content、未呼叫外部 LLM。

## Support Layers

Runtime canonical source 是 `query_planning.FIELD_REGISTRY` 與 `RUNTIME_SUPPORT_MATRIX`。支援狀態必須分層解讀：

| Field group | Parser recognizes | Plan expresses | Executor runs | Formal data exists | Slack ready |
| --- | --- | --- | --- | --- | --- |
| merchant name / handle | yes | yes | yes | yes | yes |
| Category LV1 / LV2 | yes | yes | yes | yes | yes |
| interview year / range | yes | yes | yes | yes | yes |
| exact tag / asset type | yes | yes | yes | yes | yes |
| partner name | yes when explicitly identified | yes | no | no | fail closed |
| interview date / status | yes | yes | no | no | fail closed |
| review status | yes | yes | no | no | fail closed |
| asset URL / published_at | yes | yes | no | no | fail closed |
| asset publication status | yes | yes | no | no | fail closed |

## Fail-Closed Assertions

- Unknown field and unknown operator are explicit non-match at executor level.
- Any unsupported / ambiguous / invalid hard constraint sets `execution_blocked=true`.
- Search returns before FTS/vector retrieval when execution is blocked.
- AND queries never execute only the supported subset.
- Full dates are parsed before year/range and never become an interview year.
- Parent record `status` is not copied into `StructuredAsset.publication_status`.
- Unsupported output contains no entity, asset, citation, sensitive schema detail, or stack trace.
- Parser warnings, ambiguity flags and unsupported constraints survive explicit metadata filters.

## Formal Index Gold Set

External intent leaves 103 eligible documents from the 108-document index.

| Query | Candidate after | Entities | Assets | Citations | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 三風製麵 | 1 | 1 | 2 | 2 | only requested merchant |
| dachun | 1 | 1 | 3 | 3 | 大春煉皂; article/video/podcast |
| 居家生活 | 12 | 12 | 19 | 19 | canonical category only; known false positives absent |
| 莉朵花藝 | 0 | 0 | 0 | 0 | unresolved lookup abstained |
| 2024～2025 年採訪的品牌 | 16 | 16 | 31 | 31 | years are only 2024 and 2025 |
| 已上線的影片 | 0 | 0 | 0 | 0 | publication_status unsupported |
| 某夥伴名稱＋影片 | 0 | 0 | 0 | 0 | partner_name unsupported |
| 待審核＋影片 | 0 | 0 | 0 | 0 | review_status unsupported |
| 2025-07-01 採訪的品牌 | 0 | 0 | 0 | 0 | interview_date unsupported |

## Verification

```text
234 passed, 0 failed, 0 skipped, 6 warnings in 1.53s
Python 3.9.6
```

Warnings are the pre-existing Pydantic V1-style validator deprecations. Governance, Slack, content index, LLM policy, sync, review workflow, and typed retrieval suites all passed.

## Follow-up

Asset-Level Metadata Enrichment Sprint must add reviewed per-asset URL, published_at, and publication status before these search conditions can become executable. Partner identity and interview/review status require separate schema and governance decisions. Until then they remain plan-expressible but fail closed.
