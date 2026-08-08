# Sprint 0 Readiness Checklist

本表用於正式implementation開工前及WP16 exit review。未勾選的blocking項目不得以口頭同意或skip flag繞過。

## Baseline and branch

- [ ] Audit commit pinned：`2d9a795e611fa390e7c0d066dd855fbf615887b4`。
- [ ] Frozen Audit `00`–`11`未修改。
- [ ] Decisions 1–11全部視為frozen；Remaining Decisions=None。
- [ ] Implementation branch名稱與base經確認；沒有merge/rebase/main mutation或history rewrite。
- [ ] `git status --short --untracked-files=all`已檢查，既有untracked items保持untouched。
- [ ] staged清單為空或只含當前WP明確檔案；永不使用`git add .`／`git add -A`。

## Scope and sensitive data

- [ ] `.env`、`data/`、`reports/`、`obsidian_vault/`、`.mka/`排除於Sprint 0讀寫。
- [ ] Audit外untracked review/private files未讀取、移動、刪除或重新命名。
- [ ] Synthetic fixtures only；沒有正式Google Sheet snapshot、真實口頭claim或第三方文章正文。
- [ ] No live API：沒有Google、HTTP、DNS、Slack、external LLM或credential需求。
- [ ] No production writes：沒有Vault、SQLite/FTS/vector、migration、active pointer或Apps Script寫入。
- [ ] CLI、scheduler、notification、pagination、cursor、TTL不在scope。

## Authority and identity understanding

- [ ] Google Sheets是Official metadata／identity／governance authority。
- [ ] Linked webpage只提供CapturedContent body，不決定parent identity/governance。
- [ ] MREC／BRD／MET regex、純文字、immutable/no-reuse語義已理解。
- [ ] Row/path/title/Handle/URL不是identity，只是lineage/display/evidence。
- [ ] Decision 8已理解：v1每個MREC/asset type最多一個Content Asset，key=`<MREC>:<asset_type>`，無AST。
- [ ] 0 URL+title→incomplete；1 canonical URL→candidate；2+→needs_review且不選winner。
- [ ] BRD uncertain只能needs_review；不得自動create/merge/split/assign。

## CellData and normalization

- [ ] CellData contract含formatted/effective/user-entered value、hyperlink、textFormatRuns links、dataValidation、merges。
- [ ] Formula正文只用effective/formatted value，formula string只作non-body provenance。
- [ ] Public Metric F及允許field只依merge metadata繼承。
- [ ] No blind fill-down；非merge空白保持空白。
- [ ] Source lineage含sheet/cell/range/merge anchor/fingerprint/batch，且不參與identity。
- [ ] Source fingerprint輸入/排除欄位已明確，與capture hash分離。

## Governance and minimization

- [ ] Oral-only invariant已理解：在任何persistence-ready object前不可逆排除。
- [ ] `SourceCell`與未經gate的normalized intermediary僅為暫態、不可序列化；只有WP5產出的`PersistenceEligibleMetricInput`可經factory建立`PublicMetric`。
- [ ] Oral-only claim/note/evidence/URL/raw display不進fixture、repr、exception、log、snapshot、report body、Markdown、DB、FTS、vector。
- [ ] `ExcludedSourceRef`只含safe lineage、optional MET、reason及不可逆digest。
- [ ] Restricted/pending/handle mapping不會進Official canonical set。
- [ ] Evidence authority不會升格成approved metric或擴張G-M。
- [ ] Late Slack/written-use filters只是defense in depth，不是primary oral-only gate。

## Link, URL, and capture contracts

- [ ] Link priority為rich-text → whole-cell → HYPERLINK formula → literal URL。
- [ ] Candidate priority不等於winner selection；所有distinct candidates保留provenance。
- [ ] WP6只接受WP3分類的Content Asset link cells；raw metric/oral-only cells不得形成link candidate，metric evidence link須先經WP5。
- [ ] URL validator純離線，原始URL先檢查再canonicalize；不做DNS/redirect。
- [ ] Unsafe/secret完整URL不出現在error/log/preview。
- [ ] CapturePolicy versioned、unknown fail closed；freshness threshold未配置時LKG reuse fail closed。
- [ ] CapturedContent可表達success/stale/unavailable/blocked/metadata_only/needs_review。
- [ ] Primary/Evidence parent互斥、stable relationship要求及safe HTTP metadata allowlist已理解。
- [ ] Decision 10已理解：revision可有lineage，但v1無independent refresh/activation。
- [ ] Decision 11已理解：stale保留原hash/captured_at/last-success，只更新last-attempt；URL changed不沿用舊body。

## Determinism, chunks, and release

- [ ] Synthetic HTML normalizer保留meaningful headings/paragraphs/lists/reliable tables並移除boilerplate/script。
- [ ] Content hash只由parser-versioned normalized body決定。
- [ ] Chunk ID不使用random或單獨ordinal；每chunk保留parent/authority/freshness/hash lineage。
- [ ] WP13只驗注入synthetic span的chunk metadata／identity；production splitting algorithm與heuristics明確移至後續Sprint。
- [ ] FTS/vector未來消費同一full-text chunk set，fixed summary不是唯一corpus。
- [ ] No Markdown reparse target：Official input contract不接受Markdown作authority。
- [ ] Release manifest固定metadata batch、source fingerprint、capture revisions/stale entries及全部sibling refs。
- [ ] Manifest/preview不含body、raw HTML、credential或secret。
- [ ] Sprint 0 manifest沒有write/activate/pointer/journal行為。

## Tests and stop conditions

- [ ] 每個WP的unit/contract/synthetic/deterministic/negative tests已列出。
- [ ] Testing tiers已確認：每WP只跑local targeted tests；contract checkpoints與WP16才跑相應regression，且不引用historical runtime fixtures。
- [ ] Existing regression與new target tests的語義差異已理解；不刪legacy tests掩蓋migration gap。
- [ ] Network/persistence guard與oral sentinel byte scan已納入WP0/WP16。
- [ ] 所有stop conditions已review；任一觸發時停止downstream work。
- [ ] 不使用`xfail`、skip、hardcoded fixture output或partial manifest繞過blocking failure。
- [ ] Checks若未執行會明確回報，絕不宣稱passed。

## Compatibility and rollback boundary

- [ ] `models.py`／`DocumentMetadata`保持legacy；new canonical models獨立。
- [ ] `excel-preview`、review/apply、Obsidian sync、Markdown-derived index暫時保留。
- [ ] New canonical → legacy projection只允許單向adapter與dual-run parity；不反向提升Markdown authority。
- [ ] `content_index.py`與`obsidian_sync.py`不是Sprint 0首包修改目標。
- [ ] Rollback/compatibility boundary已理解：Sprint 0未接entry point，移除new module wiring即可回復，不需資料rollback。
- [ ] 任何必須修改兩個以上legacy runtime modules的WP會停止並重新拆包。

## Final authorization gate

- [ ] Product/governance owner確認canonical schema與redacted preview足以追查且不洩漏。
- [ ] Security reviewer確認URL、oral-only、safe metadata及zero-network boundaries。
- [ ] Repository reviewer確認Likely files與actual diff一致，無scope creep。
- [ ] WP16全部safe tests實際通過並留有命令/結果證據。
- [ ] 未驗證項目、open operational policies與future decisions明確列出。
- [ ] 只有上述全部blocking項目完成後，才宣告Sprint 0 implementation ready/complete。

## Current planning-round status

- [x] Audit commit與planning branch已驗證。
- [x] Frozen Audit 00–11已完整盤點；未發現`BLOCKING_SPEC_ISSUE`。
- [x] Tracked implementation/tests/CLI/config/packaging邊界已盤點。
- [x] Work Packages、DAG、test strategy、compatibility與sequence已定義。
- [ ] Production implementation尚未開始（符合本輪要求）。
- [ ] Tests尚未執行（符合本輪要求）。
- [ ] Stage／commit／push尚未執行（符合本輪要求）。
