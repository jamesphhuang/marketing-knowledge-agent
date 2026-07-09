# Z. Read-back Validation

> 逐檔確認：名稱 / 目的 / 完整性 / 弱模型可用性 / 未完成內容。
> 位置：`/Volumes/T7/Claude Code/Marketing Knowledge Agent/fable5_governance_pack/`（共 16 檔＋本檔）。

| 檔案 | 目的 | 完整？ | 弱模型可直接使用？ | 未完成 / 依賴 |
| --- | --- | --- | --- | --- |
| A_QUICK_DIAGNOSIS.md | Top 3 系統風險 + 修法 + 測試 + 口訣；後續文件的引用基礎 | ✅ | ✅（每項有判定條件與口訣） | 無；附註表列 8 個次要問題供追蹤 |
| B_AGENT_RULES_REWRITE.md | 可直接複製的 CLAUDE.md（10 hard constraints + 路由）+ 收納原則 | ✅ | ✅ | **依賴搬移**：路由路徑在複製進專案 repo 前是 404（Z 對抗審查檢查 2） |
| C_MODEL_ROUTING_PLAYBOOK.md | 派工原則、模型等級抽換表、升降級硬規則、驗證不自驗 | ✅ | ✅ | §0 抽換表的「環境實名」欄留白，接手者填 |
| D_JUDGMENT_RUBRICS.md | 12 條判斷 rubric（判準/正例/反例/驗收/失敗處理）+ harness 極限表 | ✅ | ✅ | 無 |
| E_DELEGATION_PROMPTS.md | 9 個可直接複製的派工模板，內建 checks 與 stop conditions | ✅ | ✅ | 模板 8 需等 K 實作後才可用（已標注） |
| F_MAINTENANCE_PROTOCOL.md | 文件三層修改權限、教訓格式、精簡週期、enum/一致性維護 | ✅ | ✅ | LESSONS.md 本身尚未建立（首次踩坑時按格式新建） |
| G_LETTER_TO_FUTURE_SESSIONS.md | 交接信：隱性脈絡、退化模式、易誤判清單、下 session 優先序 | ✅ | ✅ | 含 2 個待使用者裁決問題（review_identity_mapping 語意、clean records 政策） |
| H_ARCHITECTURE_REVIEW.md | 架構優缺點、8 個具體議題、LLM guardrail 本地化原則、不做清單 | ✅ | ✅（快照文件，過期依 F 層級一修） | 無 |
| I_GOVERNANCE_RISK_REVIEW.md | 逐題 governance 檢查 + 12 項 risk register（活文件） | ✅ | ✅ | GR-1…GR-10 全部「未修」，是後續 sprint 的工作清單 |
| J_REVIEW_DECISIONS_VALIDATION_SPEC.md | 下個 sprint 規格：18 條 conflict rules、3 檔輸出、DoD | ✅ | ✅（standard-coding 可執行） | CR-18 為暫行規則，待 GR-11 裁決 |
| K_APPLY_REVIEW_DECISIONS_PREVIEW_SPEC.md | 下下個 sprint 規格：歸桶表、守恆、白名單斷言、五道防線 | ✅ | ✅ | §0 名詞定義與 §7 預設政策需使用者確認；依賴 J 完成 |
| L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md | 檢索精準度路線圖 + 5 個指定問題的具體回答 + eval 集 | ✅ | ✅ | 分數門檻 0.1 待 eval 校準（已標注） |
| M_ADMIN_USABILITY_REVIEW.md | 管理者痛點 + 快速/中期/長期改善 + 不做清單 | ✅ | ✅ | 無 |
| Z_ADVERSARIAL_REVIEW.md | 對本包的對抗審查：5 項發現已修、6 項留存事項 | ✅ | ✅ | 建議下 session 用真 fresh context 重跑一次 |
| Z_READBACK_VALIDATION.md | 本檔 | ✅ | ✅ | 無 |
| Z_ONE_PAGE_SUMMARY.md | 一頁總結與明日使用指南 | ✅（最後寫成） | ✅ | 無 |

## 交叉一致性抽查

- 使用者 brief 要求的全部交付項（A–M、Z×3、G）**無缺漏**，檔名逐字符合。
- hard constraints（B）↔ D rubrics ↔ J/K 規則：restricted / pending / suggested_action / preview-only 四主題在三處的表述一致（對抗審查 F-1…F-3 修正後）。
- 使用者 brief 中「Review Decisions Validation Sprint 尚未完成」與 pack 內 CURRENT_STATUS「已完成」的矛盾：已在 J 開頭與 B §5 明確處理（以 pack 為準，J 定位為補強規格）。
- 缺料聲明集中在 A 文件末節，G 信與 Z 對抗審查末節重申，無編造 pack 中不存在的內容。
