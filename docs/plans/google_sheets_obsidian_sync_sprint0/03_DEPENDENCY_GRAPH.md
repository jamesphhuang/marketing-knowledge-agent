# Sprint 0 Dependency Graph

## ASCII DAG

```text
WP0 Offline Test Harness
├── WP1 CellData DTO + Reader Protocol
│   ├── WP2 Snapshot Serialization + Fingerprint
│   ├── WP3 Merge-aware Normalization + Lineage
│   │   └── WP6 Eligible Asset Link Extraction
│   │       └── WP7 URL Safety + Canonicalization
│   │           └── WP8 Asset Resolution
├── WP4 Permanent Identity + Persistence-Eligible Schemas
├── WP9 CapturedContent DTO
├── WP10 CapturePolicy + Fetch Outcome Contract
└── WP11 Synthetic HTML Normalization

WP3 + WP4 ────────────────────────────────> WP5 Oral-only Early Minimization
WP3 + WP4 + WP6 + WP7 ───────────────────> WP8 Asset Resolution
WP7 + WP9 + WP10 + WP11 ─────────────────> WP12 Content Hash + Revision/LKG
WP9 + WP12 ───────────────────────────────> WP13 Captured Chunk Metadata + Identity
WP2 + WP4 + WP8 + WP9 + WP10 + WP12 + WP13
  └───────────────────────────────────────> WP14 Release Manifest
WP5 + WP8 ────────────────────────────────> WP15 Redacted Preview
WP14 + WP15 + WP0–WP13 complete ─────────> WP16 Integration Tests
```

## Required sequencing

- WP0先完成，後續fixture與guard才有共同安全基座。
- WP1先於WP2、WP3；WP3 field classification完成後才可把eligible Content Asset cells交給WP6。
- WP6先於WP7，WP7先於WP8；extract、safety、cardinality不可混成一個不可review步驟。WP6拒絕raw metric/oral-only cells；metric evidence links必須先經WP5且不在本WP建立。
- WP3與WP4都完成後才能建立WP5 early minimization；原始`SourceCell`或未經gate的normalized payload不得先建立`PublicMetric`。
- WP4、WP6、WP7、WP3完成後才能做WP8 Content Asset resolution。
- WP9、WP10、WP11可在WP0後平行；WP12必須等WP7、WP9、WP10、WP11，並單獨擁有LKG eligibility/composition。
- WP13必須等WP9與WP12；本Sprint只建立chunk metadata／identity contract，production splitting algorithm延後。
- WP14必須等metadata fingerprint、canonical IDs、asset resolution、capture DTO/policy/hash/chunk metadata均穩定。
- WP15只消費already-redacted/validated inputs，依賴WP5與WP8；可與WP14平行並在WP16匯合。
- WP16是最後的composition gate，不得用test-only bypass填補尚未完成的WP。

## Parallel work lanes

WP0完成後可開四條獨立lane：

1. **Google snapshot lane**：WP1 → WP2；WP1 → WP3。
2. **Canonical governance lane**：WP4，之後與WP3匯合做WP5。
3. **Link lane**：WP1 → WP3 → WP6 → WP7 → WP8；WP4在WP8前匯合。
4. **Captured content lane**：WP9、WP10、WP11可平行；WP7／WP9／WP10／WP11匯合後做WP12 → WP13。

WP14與WP15可在各自upstream穩定後平行實作；WP16是唯一最後匯流點。

## Blocking relationships

| WP | Directly blocks |
| --- | --- |
| WP0 | WP1–WP16的安全測試 |
| WP1 | WP2、WP3 |
| WP2 | WP14、WP16 |
| WP3 | WP5、WP6、WP8、WP16 |
| WP4 | WP5、WP8、WP14、WP16 |
| WP5 | WP15、WP16 |
| WP6 | WP7、WP8、WP16 |
| WP7 | WP8、WP12、WP16 |
| WP8 | WP14、WP15、WP16；WP9 primary binding只在WP16整合 |
| WP9 | WP12、WP13、WP14、WP16 |
| WP10 | WP12、WP14、WP16 |
| WP11 | WP12、WP16 |
| WP12 | WP13、WP14、WP16 |
| WP13 | WP14、WP16 |
| WP14 | WP16 |
| WP15 | WP16 |
| WP16 | Sprint 0 exit |

## Graph-level stop rule

若任何upstream WP觸發stop condition，下游WP不得以臨時dict、fixture hardcode或skip flag繞過。尤其WP5、WP7、WP14失敗時，integration lane必須停止，不能先做preview或manifest假資料宣稱ready。
