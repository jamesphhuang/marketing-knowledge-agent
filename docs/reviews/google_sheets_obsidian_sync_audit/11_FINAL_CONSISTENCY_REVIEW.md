# Final Consistency Review

## 1. Review scope

本文件是`00_EXECUTIVE_SUMMARY.md`至`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`與Decision 1–11的最終文件一致性審查，不是新architecture spec、implementation sprint或Decision 12。審查範圍包括authority、canonical flow、Google schema、identity、governance、linked capture、freshness、Search／RAG、release atomicity、Slack surfaces、Sprint 0與requirements traceability。

審查只判斷Audit文件是否互相一致，以及target與tracked current state是否清楚分離。未修改`src/`、`tests/`、Vault或Google Sheets，未執行正式測試、API、capture、migration、index build、Slack、stage、commit或push。

## 2. Baseline

Final Consistency Review開始時的Git baseline：

- branch：`codex/audit/google-sheets-obsidian-sync`
- HEAD：`fdde39f0d928a97a7692a658238e0bdaaca4450e`
- HEAD message：`docs: add Google Sheets Obsidian sync audit`
- parent／local main／merge-base：`11c99c86ccbbab06f2bf583f8918560d0ce4e985`
- cached：empty
- tracked Audit modifications：`00`、`02`、`03`、`04`、`05`、`06`、`07`、`08`、`09`
- untracked Audit file：`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`
- Audit外既有untracked files：保持untouched

`01_CURRENT_STATE_AUDIT.md`與`09_COMMANDS_AND_EVIDENCE.md`中的`11c99c8...`是原始Audit盤點時點，不是本次Final Review的HEAD。原始Audit曾記錄431個安全測試通過；本次Final Review依明確限制未重跑正式測試。

## 3. Decision 1–11 matrix

| Decision | Confirmed choice | Final consistency |
| --- | --- | --- |
| 1 | A：dedicated read-only Service Account；最小唯讀scope，無Google write capability | consistent |
| 2 | C：Slack／internal search是Official retrieval surface，不是exposure channel；不新增Slack checkbox | consistent |
| 3 | A：Manual Enrichment `approved_by`須以stable canonical reviewer ID exact-match受控whitelist；失敗fail closed | consistent |
| 4 | B：permanent ID writer採external standalone Apps Script；不採spreadsheet-bound production architecture | consistent |
| 5 | A：final sync／Release failure以Private Slack Ops為primary alert；release state先durable，notification後執行 | consistent |
| 6 | B：Content與Public Metric分開item caps，另有overall `rendered_message_budget` | consistent |
| 7 | A：總共最多3次attempt，Asia/Taipei 09:00／09:30／10:00，沒有10:30第四次 | consistent |
| 8 | A：v1每個H–K cell最多一個logical Content Asset；identity為`<MREC>:<asset_type>`；無AST ID | consistent |
| 9 | A：核准linked article建立`CapturedContent`全文，產生Obsidian／FTS／vector sibling outputs與query-focused RAG | consistent |
| 10 | A：v1 linked capture隨Google metadata sync建立同一完整Release；無independent capture activation | consistent |
| 11 | A：same canonical URL temporary failure在previous-success、policy、security、governance與freshness gates通過後，可讓stale LKG進新完整Release | consistent |

`08_DECISIONS_REQUIRED.md`已將1–11全部列為Confirmed，Remaining Decisions為`None`。Open implementation、operational、governance與future architecture questions不會重新打開Decision 1–11。

## 4. Authority consistency

Final authority model一致如下：

- Google Sheets：business metadata、permanent relationships與reviewed governance authority。
- Google Sheet C「論述」：Public Metric approved claim authority。
- Linked webpage：Content Asset／Evidence body source，不是identity、parent relation、metric claim或governance authority。
- Primary Content：`authority_role=primary_content`，parent為Content Asset。
- Evidence Article：`authority_role=evidence`，parent為MET evidence relationship；不得升格為`approved_metric`或擴張G-M。
- Manual Enrichment：獨立approved enrichment authority，須通過external whitelist與semantic approval gate；不得覆寫或冒充Official。
- GitHub：code與specification authority，尤其是standalone Apps Script source；deployment editor drift不是authority。
- Obsidian：human-readable sibling projection，不是Official index authority。
- SQLite／FTS／vector：normalized canonical metadata與相容CapturedContent的sibling outputs。
- Slack Internal Search：user retrieval surface。
- Private Slack Ops：maintainer-only operational notification surface。

Target architecture沒有`Google Sheet → Markdown → parse Markdown → Official Index`。這條flow只出現在`00`、`01`、`02`與`09`的CURRENT STATE／IMPLEMENTATION GAP證據，以及migration期間的legacy parity比較。

## 5. Data-flow consistency

Target canonical flow一致為：

```text
Google Sheets
  → dedicated read-only CellData snapshot
  → merge-aware extraction / normalization
  → early governance minimization
  → permanent-ID / relationship normalization
  → canonical metadata
  → URL validation / Link Resolver
  → versioned Capture Policy
  → frozen CapturedContent candidate
  → normalized canonical release model
  → sibling Obsidian + SQLite/FTS + vector artifacts
  → validation
  → complete Release activation
  → internal retrieval
  → governance / intent / requested-channel gates
  → dedupe / rerank
  → response budget / rendering
  → query-focused answer with citations
```

Standalone Apps Script是獨立、限欄位、限target的permanent-ID write control plane，不位於read／sync／capture pipeline。Private Slack Ops notification只在final release／batch result已durable後作side effect，不位於release transaction。

## 6. Identity consistency

| Entity | Identity | Final rule |
| --- | --- | --- |
| Source Record | MREC | immutable、no reuse、duplicate fail closed |
| Brand | BRD | semi-auto candidate grouping＋human approval；approved mapping才可controlled backfill |
| Public Metric | MET | immutable、no reuse；Google C claim的stable parent identity |
| Content Asset v1 | `<MREC>:<asset_type>` | 每個MREC／asset type最多一筆；URL與candidate position不參與identity |
| Manual Enrichment | ENR | separate enrichment identity，不能冒充Official |

Row number、`ROW()`、filename、path、title、URL、Rich Text run position與array index都只可作lineage／display／candidate provenance，不是permanent identity。Decision 8的v1沒有AST ID或Asset ID allocator。未來若一個cell需要多個正式assets，必須另開architecture／migration decision。

Blank BRD不得自動建立canonical brand；same name alone不足以merge。MREC／MET既有合法ID不得覆寫，archived／retired ID不得重用，duplicate／mutation／namespace conflict一律fail closed。

## 7. Governance consistency

- Oral-only在任何persistable serialization前轉為redacted exclusion reference；正文、備註、evidence與URL不得進Markdown、SQLite、FTS、vector、Slack Search、Slack Ops raw body、reports body、debug dump、logs或正式敏感fixtures。
- Pending不進Official Index。
- Restricted不進一般Official Search，並沿用更嚴的restricted governance。
- Evidence不得擴張MET claim authority或G-M permissions。
- Manual Enrichment與Official index分離；`approved_by`／whitelist／semantic hash任一失敗即search eligibility fail closed。
- 後期redaction只是defense in depth，不是oral-only的主要保護機制；主要保護是normalization時early minimization。

Decision 2的兩層模型一致：第一層決定persistence／search eligibility；第二層只在query要求特定usage時套G-M exposure permission。Generic internal query不要求任一G-M為true。

正式Google schema已核對：

- 商家／夥伴案例資料庫A–L依序為採訪年份、狀態、商家／夥伴名稱、Handle、Sales Category LV1、Sales Category LV2、內容相關標籤、文章、影片、Podcast、新聞、備註；planned M／N／O為MREC／BRD／ID Review Status。
- 「可公開」對外數據A–M依序為類型、指標、論述、備註、更新時間、參考新聞連結、新聞稿、自媒體、Saleskits、口頭說明、演講簡報、官網／招募網站、廣告；planned N為MET。
- Decision 2沒有Slack column，MET沒有移到O，G-M只表示usage／exposure channels。
- Formula內容採effective／formatted value；F與其他merge-aware欄位只依實際merge metadata繼承，不blind forward-fill。

## 8. Linked-content consistency

Link candidates的統一順序為：

1. `textFormatRuns[].format.link.uri`
2. whole-cell hyperlink
3. `HYPERLINK` formula
4. literal safe HTTP／HTTPS URL

所有候選先做安全檢查、canonicalization與dedupe。H–K單一cell得到0個safe URL但有title時建立`incomplete`且`searchable=false`；1個distinct safe canonical URL可進後續eligibility；2個以上則`needs_review`，不得pick winner、auto-split或改用URL／position作identity。

Primary Article flow為`Content Asset → CapturedContent(primary_content) → clean full body → deterministic chunks → sibling Obsidian／FTS／vector`。Public Metric F flow為`MET → evidence relationship → CapturedContent(evidence)`；Evidence正文不得自動建立approved Public Metric claim。

## 9. Release/freshness consistency

兩個freshness domains明確分離：

- `source_fingerprint`：Google canonical metadata state。
- `capture_content_hash`／`content_hash`：parser-versioned normalized webpage body state。

Hash分離不等於可獨立publish。Decision 10的v1完整Release固定：

- `release_id`
- `metadata_sync_batch_id`
- source fingerprint
- release-pinned CapturedContent revisions／hashes
- Obsidian projection
- SQLite／FTS
- vector index

發布模型是staged immutable artifacts＋validation＋single global active Release pointer activation＋rollback to prior complete release，不虛稱跨filesystem／database ACID transaction。Production不允許Vault A、FTS B、vector C的混合composition。

Decision 11 stale LKG只在same canonical URL、previous success、temporary failure、policy／security／governance允許且freshness gate通過時進candidate。沿用原`content_hash`、`captured_at`、`last_successful_capture_at`，更新`last_capture_attempt_at`並標`capture_status=stale`；URL changed時不得把舊body掛到新URL。具體freshness threshold仍是open operational policy，不表示Decision 11未完成。

## 10. Search/RAG consistency

Eligible captured full-body chunks是正式searchable corpus。Title、tags與fixed／precomputed summary只能輔助，不能取代全文。

正式answer flow為：

```text
eligible corpus
  → retrieval
  → authority / governance
  → query intent
  → requested exposure channel when applicable
  → dedupe
  → rerank
  → content_item_cap + metric_item_cap
  → rendered_message_budget
  → atomic rendering
  → query-focused answer from retrieved evidence
```

AI query summary是ephemeral derived answer，不寫回Official body、不建立新Public Metric authority，也不得補入retrieved evidence未支持的內容。

Decision 6 caps只消耗post-governance、post-intent、post-rerank eligible results。Wrong-channel、oral-only、restricted general-search blocked、pending、non-searchable與其他fail-closed results不占quota。Public Metric claim、identity、citation、authority與allowed-channel metadata必須作atomic whole item；放不下時整筆略過並回傳more-results等價訊號。Shown／remaining counts只基於eligible set，且更嚴格的sensitive aggregate policy優先。Display caps不縮小ingestion、Official Index、FTS、vector或retrieval corpus。

## 11. Slack/search/ops consistency

- Slack Internal Search：Official／explicit Enrichment knowledge retrieval與answer rendering；套Decision 2與6。
- Private Slack Ops：final operational failure notification；不套knowledge-result pagination、content cap或metric cap。
- Attempt 1／2只記retry state；Attempt 3仍失敗後先durably寫`release_status=failed`，再嘗試Slack Ops。
- `release_status`與`notification_status`分離；Slack failure不得rollback成功Release、改寫release result或抹掉failure evidence。
- Ops payload只含sanitized identifiers、stage、category、counts與status，不含正文、claim、raw HTTP／HTML、credential、secret或unredacted stack trace。

## 12. Sprint 0 scope validation

Sprint 0仍受控，包含：

- synthetic Google reader／CellData DTO與injected fixtures
- merge-aware normalization contract
- BRD／MREC／MET／ENR、lineage、lifecycle與eligibility DTO
- URL validator／resolver contract
- oral-only early minimization
- CapturedContent／CapturePolicy／fetch protocol DTO
- synthetic HTML normalization、content hash與chunk metadata contracts
- release manifest DTO／contract
- redacted previews與完全synthetic／offline tests

Sprint 0明確不包含：

- live Google credential／API
- standalone Apps Script implementation、deployment或real ID writes
- HTTP crawling／production capture
- production Obsidian write
- production SQLite migration／vector build
- Slack、scheduler、pagination UX、cursor或TTL

Decision 8–11沒有把live integration或production mutation塞進Sprint 0。

## 13. Traceability validation

`02_REQUIREMENTS_TRACEABILITY.md`持續使用`implemented`、`partial`、`missing`、`conflicting`、`unknown`描述tracked code evidence，不以文件已確認取代implementation證據。近期Decisions 2、3、4、5、6、8、9、10、11均未被誤標為implemented。

重點gap狀態：

| Gap | Current status |
| --- | --- |
| Local Excel preview、existing Obsidian rollback、SQLite／FTS／deterministic vector、Slack Bot基礎 | implemented／partial，僅代表現行能力 |
| Google CellData adapter／read-only auth integration | missing |
| Row-derived identity相對MREC／BRD／MET target | conflicting／missing |
| Oral-only persistence boundary | conflicting |
| Markdown-derived formal index相對canonical sibling target | conflicting |
| CapturedContent、HTML normalization與capture policy runtime | missing |
| External standalone Apps Script writer | missing |
| Complete Release coordinator／global active pointer | missing／partial |
| Private Slack Ops notifier與state separation | missing |
| Decision 2 intent-aware retrieval／exposure split | conflicting |
| Decision 6 independent metric cap／rendered budget | conflicting／missing |

## 14. Current vs target state

- CURRENT STATE：local `.xlsx` preview、row-derived IDs、Obsidian Markdown-derived formal index、oral-only可先落地後由Slack擋、shared renderer caps，以及分散的Vault／DB lifecycle。
- TARGET STATE：read-only Google CellData、permanent IDs、early minimization、canonical metadata＋CapturedContent、sibling renderers、complete Release pointer、intent-aware governance與atomic response budget。
- IMPLEMENTATION GAP：由`02`與`09`以tracked code／tests證據記錄；舊行為不是target endorsement。

`01`與`09`已明示其baseline／test evidence屬原始Audit時點，避免與本次Final Review的HEAD及未執行正式測試混淆。

## 15. Open implementation / operational questions

以下不是Remaining Decisions，也不阻擋Sprint 0 contract工作。

### Implementation details

- canonical reviewer ID的具體表示與whitelist storage interface
- `clasp`、Apps Script CI/CD、deployment source packaging
- Slack App／workspace／channel configuration與identity binding
- `content_item_cap`、`metric_item_cap`、`rendered_message_budget`的實際值與計量方式
- pagination cursor／page token／TTL／button／message splitting
- capture error classifier、parser與domain policy configuration格式

### Operational policies

- stale LKG freshness threshold、policy owner與review cadence
- stale retrieval penalty／warning UX
- permanent 404／removed後LKG retention與search eligibility
- Apps Script deployment owner、execute-as與credential／secret供應
- notification retry／backoff、escalation、dead-letter與Email fallback
- success notification／digest／health message
- third-party domain allowlist、review cadence、robots／legal validation owner

### Future governance decisions

- canonical reviewer identifier scheme的組織authority
- approver移出whitelist後的retroactive revocation或approval-time snapshot semantics
- whitelist authority owner與change-approval process
- 首次capture unavailable時，metadata release是否可active但不搜尋全文

### Future architecture options

- independent capture scheduler／capture batch
- independent revision activation、partial content release與composition manifest
- cross-revision rollback與stale replacement policy
- JavaScript-rendered pages、非HTML、影片／Podcast transcript support
- Content subtype-specific cap／ratio或independent pagination

No new blocking architecture decision discovered.

## 16. Blocking inconsistencies found

Final review沒有發現Decision 1–11之間的blocking architecture contradiction。發現的是可直接修正的文件一致性問題：兩處仍把已確認事項寫成未決、historical baseline／test wording可能被誤讀為本輪狀態，以及一處release traceability仍只用`sync_batch_id`表達完整composition。全部已以最小文字修正；沒有重開Decision或新增需求。

## 17. Corrections made

| File | Issue | Why conflicting | Minimal correction |
| --- | --- | --- | --- |
| `00_EXECUTIVE_SUMMARY.md` | 仍寫完成Sprint後再「取得」Decision裁決；測試結果未標示歷史時點 | Decision 1–11已全部確認；本輪禁止正式測試 | 改為遵守已確認Decisions，並標示431 tests是原始Audit evidence |
| `01_CURRENT_STATE_AUDIT.md` | baseline未標示historical；Decision 2／6 implementation gap仍以舊「mapping／未決」語句表達 | 容易把current code缺口誤讀為design未決 | 標示原始baseline；改成intent split與independent metric cap尚未實作 |
| `02_REQUIREMENTS_TRACEABILITY.md` | `AUTH-04`只要求siblings共用`sync_batch_id` | Decision 10要求完整Release固定metadata batch與capture composition | 改成共用`release_id`並由manifest固定metadata batch、fingerprint與capture revisions |
| `03_PROPOSED_SYNC_ARCHITECTURE.md` | immutable release directory／manifest仍以`sync_batch_id`作主要key | 容易弱化global complete Release identity | 目錄改用`release_id`，manifest明列`metadata_sync_batch_id`與release-pinned capture composition |
| `09_COMMANDS_AND_EVIDENCE.md` | baseline／驗證措辭未明示為原始Audit時點 | 容易與Final Review HEAD及未執行正式測試混淆 | 僅加historical說明與11號文件交叉引用 |

## 18. Final readiness conclusion

A. Decision 1–11全部一致：Yes。

B. Remaining Decisions：None。

C. Blocking architecture contradiction：None after review；沒有發現需要Decision 12的缺口。

D. Sprint 0 scope仍受控：Yes，只含contracts、DTO、synthetic fixtures、offline validation與redacted preview。

E. Target architecture依賴Markdown reparse：No；Markdown-derived index只保留為current-state evidence／migration parity path。

F. Oral-only在persistence前排除：Yes，target contract要求early irreversible minimization。

G. Official／Primary／Evidence／Enrichment authority清楚：Yes。

H. Release composition可追蹤及rollback：Design complete；manifest固定metadata／capture／artifacts，以global pointer activation與previous complete release rollback表達，但tracked generic coordinator尚未實作。

I. Implementation missing而非design unresolved的主要項目：Google adapter、permanent-ID writer、CapturedContent runtime、canonical sibling renderers、complete Release coordinator、Slack Ops notifier、Decision 2 intent split與Decision 6 response budget。

J. Audit ready for commit／review：Yes at documentation level，前提是最終Git whitespace／cached validation維持乾淨；本輪不執行stage、commit或push。

## 19. Git evidence

本次Final Review實際執行：

```text
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git diff --cached --name-only
git diff --name-only -- docs/reviews/google_sheets_obsidian_sync_audit/
git diff --check -- docs/reviews/google_sheets_obsidian_sync_audit/
git diff -- docs/reviews/google_sheets_obsidian_sync_audit/
```

Baseline時`git diff --cached --name-only`完全空白。`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`與本文件保持untracked並另以`git diff --no-index --check`等價方式檢查whitespace；未stage。Audit外既有及新出現的untracked files均未處理。
