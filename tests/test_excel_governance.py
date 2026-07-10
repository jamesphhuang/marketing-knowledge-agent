from datetime import date

import pytest
from pydantic import ValidationError

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.excel_ingestion import (
    EXCEL_INGESTION_PLAN,
    SHEET_RESTRICTED_CUSTOMERS,
    build_handle_mapping,
    normalize_handle_mapping_row,
    normalize_merchant_case_row,
    normalize_public_metric_row,
)
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord, apply_governance_to_answer
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Citation, Document, DocumentMetadata, GeneratedAnswer, SearchFilters
from marketing_knowledge_agent.pipeline import agent_ask, ask_index, search_index
from marketing_knowledge_agent.cli import main


CAPTURED_DATE = date(2026, 7, 1)


def test_restricted_customer_is_governance_table_not_search_citation(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            {
                "title": "Secret Brand",
                "source_type": "database",
                "record_type": "restricted_customer",
                "status": "published",
                "publish_date": CAPTURED_DATE,
                "brand_name": "Secret Brand",
                "data_classification": "restricted",
                "can_quote_externally": False,
                "source_sheet": SHEET_RESTRICTED_CUSTOMERS,
                "source_row": 4,
            }
        ],
    )

    results = search_index("Secret Brand", db_path=db_path, limit=5)
    answer = ask_index("Secret Brand", db_path=db_path, limit=5)

    assert results == []
    assert answer.citations == []


def test_agent_ask_redacts_body_mention_when_query_is_clean(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _public_metric_payload(
                title="Case mention",
                claim_statement="Secret Brand had a campaign result in a draft note.",
                allowed_exposure_channels=["saleskits"],
            )
        ],
    )
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name="Secret Brand")])

    answer = agent_ask(
        "請整理 campaign result",
        db_path=db_path,
        governance_index=governance_index,
    )

    assert answer.warnings
    assert "不可對外引用 / 不可公開提及" in answer.warnings[-1]
    assert "Secret Brand" not in answer.answer
    assert "[restricted customer]" in answer.answer


def test_cli_ask_drops_identity_hit_source_when_query_is_clean(tmp_path, capsys):
    vault = tmp_path / "vault"
    (vault / "blog").mkdir(parents=True)
    restricted_brand = "Restricted Brand Alpha"
    _write_markdown(
        vault / "blog" / "restricted-brand-alpha.md",
        f"""---
title: "{restricted_brand} launch note"
source_type: blog
status: published
publish_date: 2026-07-01
source_path: "blog/restricted-brand-alpha.md"
---

{restricted_brand} should never appear in public-facing answer content.
""",
    )
    db_path = tmp_path / "index.sqlite"
    restricted_path = _write_restricted_customers(tmp_path, [restricted_brand])
    from marketing_knowledge_agent.pipeline import ingest_vault

    ingest_vault(vault, db_path)

    exit_code = main(
        [
            "ask",
            "launch note",
            "--db",
            str(db_path),
            "--restricted-customers",
            str(restricted_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert restricted_brand not in output
    assert "找不到符合條件" in output
    assert "已依 restricted denylist 移除" in output


def test_citation_removed_when_title_hits_denylist():
    restricted_brand = "Restricted Brand Beta"
    answer = GeneratedAnswer(
        question="請整理案例",
        answer="以下是根據 Marketing Knowledge Vault 檢索到的初步回答：\n[1] General snippet",
        citations=[
            Citation(
                label="[1]",
                title=f"{restricted_brand} approved-looking title",
                source_path="blog/general.md",
                chunk_id="chunk-1",
                status="published",
                source_type="blog",
                record_type="content_asset",
                data_classification="public",
                can_quote_externally=True,
                publish_date="2026-07-01",
                freshness_note="最新日期 2026-07-01；距今約 0 天。",
            )
        ],
    )
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_brand)])

    governed = apply_governance_to_answer(answer, governance_index)

    assert governed.citations == []
    assert any("已依 restricted denylist 移除 1 筆引用來源" in warning for warning in governed.warnings)


def test_ask_warns_when_denylist_missing(tmp_path):
    db_path = _build_index(tmp_path, [_public_metric_payload()])

    answer = ask_index(
        "公開指標",
        db_path=db_path,
        restricted_customers_path=tmp_path / "missing_restricted_customers.json",
    )

    assert answer.citations
    assert answer.governance_checked is False
    assert any("restricted denylist 未載入" in warning for warning in answer.warnings)


def test_answer_has_governance_checked_flag(tmp_path):
    db_path = _build_index(tmp_path, [_public_metric_payload()])
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name="Restricted Brand Gamma")])

    answer = ask_index("公開指標", db_path=db_path, governance_index=governance_index)

    assert answer.governance_checked is True


def test_short_alias_requires_word_boundary():
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name="HR")])

    assert not governance_index.check_text("CHR platform overview").blocked
    assert not governance_index.check_text("through-channel campaign").blocked
    assert governance_index.check_text("HR").blocked
    assert governance_index.check_text("HR campaign").blocked
    assert governance_index.check_text("campaign for HR-brand").blocked


def test_answer_body_scrubbed_when_source_hits_denylist(tmp_path):
    restricted_brand = "Restricted Brand Delta"
    db_path = _build_index(
        tmp_path,
        [
            _content_asset_payload(
                title="Clean campaign guide",
                claim_statement="campaign result CLEAN_MARK safe guidance",
            ),
            _content_asset_payload(
                title=f"{restricted_brand} private campaign note",
                claim_statement="campaign result LEAK_MARK confidential operational detail",
            ),
        ],
    )
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_brand)])

    answer = ask_index("campaign result", db_path=db_path, governance_index=governance_index, limit=5)

    assert "LEAK_MARK" not in answer.answer
    assert all(restricted_brand not in citation.title for citation in answer.citations)
    assert "CLEAN_MARK" in answer.answer
    assert any(citation.title == "Clean campaign guide" for citation in answer.citations)
    assert any("已依 restricted denylist 移除" in warning for warning in answer.warnings)


def test_agent_ask_answer_body_scrubbed_when_source_hits_denylist(tmp_path):
    restricted_brand = "Restricted Brand Epsilon"
    db_path = _build_index(
        tmp_path,
        [
            _content_asset_payload(
                title="Clean synthesis guide",
                claim_statement="campaign result CLEAN_MARK safe synthesis detail",
            ),
            _content_asset_payload(
                title=f"{restricted_brand} private synthesis note",
                claim_statement="campaign result LEAK_MARK confidential synthesis detail",
            ),
        ],
    )
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_brand)])

    answer = agent_ask("整理 campaign result 素材", db_path=db_path, governance_index=governance_index, limit=5)

    assert "LEAK_MARK" not in answer.answer
    assert all(restricted_brand not in citation.title for citation in answer.citations)
    assert "CLEAN_MARK" in answer.answer
    assert any(citation.title == "Clean synthesis guide" for citation in answer.citations)
    assert any("已依 restricted denylist 移除" in warning for warning in answer.warnings)


def test_public_metric_filters_by_allowed_exposure_channel(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _public_metric_payload(
                title="Saleskit metric",
                metric_name="導入成效",
                claim_statement="導入後詢問數增加 300%",
                allowed_exposure_channels=["saleskits", "verbal_briefing"],
            ),
            _public_metric_payload(
                title="Ads metric",
                metric_name="廣告素材",
                claim_statement="廣告可使用的公開數據",
                allowed_exposure_channels=["ads"],
            ),
        ],
    )

    results = search_index(
        "公開數據",
        db_path=db_path,
        filters=SearchFilters(record_type=["public_metric"], exposure_channel=["saleskits"]),
        limit=5,
    )

    assert results
    assert all("saleskits" in result.chunk.metadata.allowed_exposure_channels for result in results)
    assert all(result.chunk.metadata.title != "Ads metric" for result in results)

    press_release_results = search_index(
        "導入成效",
        db_path=db_path,
        filters=SearchFilters(record_type=["public_metric"], exposure_channel=["press_release"]),
        limit=5,
    )
    assert press_release_results == []


def test_pending_metric_is_not_returned_for_external_quote_filter(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            {
                "title": "待確認會員數據",
                "source_type": "database",
                "record_type": "pending_metric",
                "status": "draft",
                "publish_date": CAPTURED_DATE,
                "metric_type": "會員規模",
                "metric_name": "品牌會員總數",
                "claim_statement": "品牌總會員數累積超過 XX 百萬人",
                "claim_status": "pending_review",
                "data_classification": "internal",
                "can_quote_externally": False,
                "source_sheet": "待確認數據",
                "source_row": 3,
            }
        ],
    )

    external_results = search_index(
        "品牌會員總數",
        db_path=db_path,
        filters=SearchFilters(can_quote_externally=True),
        limit=5,
    )
    internal_answer = ask_index(
        "品牌會員總數",
        db_path=db_path,
        filters=SearchFilters(record_type=["pending_metric"]),
        limit=5,
    )

    assert external_results == []
    assert internal_answer.citations
    assert any("待確認數據" in warning for warning in internal_answer.warnings)


def test_merchant_case_closed_status_generates_warning_and_source_trace(tmp_path):
    payload = normalize_merchant_case_row(
        {
            "採訪年份": "2025",
            "狀態": "已關店、轉走/結束合作關係",
            "商家 / 夥伴名稱": "金魚藝術展",
            "Handle": "-",
            "Sales Category LV1": "已關閉",
            "Sales Category LV2": "已關閉",
            "內容相關標籤": "QRedeem",
            "文章": "日本人氣金魚藝術展案例",
            "影片": "日本人氣金魚藝術展影片",
            "Podcast": "-",
            "新聞": "",
            "備註": "停止營運，保留文章",
        },
        source_row=8,
        captured_date=CAPTURED_DATE,
    )
    db_path = _build_index(tmp_path, [payload])

    answer = ask_index("金魚藝術展", db_path=db_path, filters=SearchFilters(record_type=["merchant_case"]))

    assert answer.citations
    assert answer.citations[0].record_type == "merchant_case"
    assert answer.citations[0].source_sheet == "商家夥伴案例資料庫"
    assert answer.citations[0].source_row == 8
    assert any("商家狀態或備註顯示風險" in warning for warning in answer.warnings)


def test_normalizes_empty_asset_fields_and_handle_mapping_enrichment():
    handle_record = normalize_handle_mapping_row(
        {
            "Handle": "1982kidsstore",
            "Name (with Link)": "1982kids",
            "Lv1 Sales Category": "旅遊＆文創服務",
            "Lv2 Sales Category 1st": "藝術文創/圖書文具/宗教",
        },
        source_row=2,
        captured_date=CAPTURED_DATE,
    )
    mapping = build_handle_mapping([handle_record])

    payload = normalize_merchant_case_row(
        {
            "採訪年份": "2026",
            "狀態": "已上架",
            "商家 / 夥伴名稱": "",
            "Handle": "1982kidsstore",
            "Sales Category LV1": "",
            "Sales Category LV2": "",
            "內容相關標籤": "OMO, 會員經營",
            "文章": "-",
            "影片": "暫時下架",
            "Podcast": None,
            "新聞": "審核中",
            "備註": "",
        },
        source_row=10,
        captured_date=CAPTURED_DATE,
        handle_mapping=mapping,
    )

    assert payload["article_title"] is None
    assert payload["video_title"] is None
    assert payload["podcast_title"] is None
    assert payload["news_title"] is None
    assert payload["no_valid_content_asset"] is True
    assert payload["can_enter_content_index"] is False
    assert payload["can_quote_externally"] is False
    assert payload["invalid_asset_fields"] == ["影片", "新聞"]
    assert payload["invalid_asset_values"] == {"影片": "暫時下架", "新聞": "審核中"}
    assert "no_valid_content_asset" in payload["governance_risk_reasons"]
    assert "can_quote_externally=false" in payload["governance_risk_reasons"]
    assert payload["brand_name"] == "1982kids"
    assert payload["sales_category_lv1"] == "旅遊＆文創服務"
    assert payload["sales_category_lv2"] == "藝術文創/圖書文具/宗教"
    assert payload["content_tags"] == ["omo", "會員經營"]


def test_merchant_case_notes_and_competitor_risks_keep_reasons():
    payload = normalize_merchant_case_row(
        {
            "採訪年份": "2025",
            "狀態": "現有商家",
            "商家 / 夥伴名稱": "DEER W.",
            "Handle": "deerw",
            "Sales Category LV1": "美食",
            "Sales Category LV2": "",
            "內容相關標籤": "",
            "文章": "已下架",
            "影片": "-",
            "Podcast": "-",
            "新聞": "-",
            "備註": "智慧廣告系統停用，下架內容，轉至 Shopify",
        },
        source_row=116,
        captured_date=CAPTURED_DATE,
    )

    assert payload["invalid_asset_fields"] == ["文章"]
    assert payload["invalid_asset_values"] == {"文章": "已下架"}
    assert "notes_governance_risk" in payload["governance_issue_types"]
    assert "competitor_migration" in payload["governance_issue_types"]
    assert "備註 contains 智慧廣告系統停用" in payload["governance_risk_reasons"]
    assert "備註 contains Shopify" in payload["governance_risk_reasons"]


def test_public_metric_row_normalizes_boolean_channels():
    payload = normalize_public_metric_row(
        {
            "類型": "合作夥伴",
            "指標": "夥伴成效",
            "論述": "合作後每月詢問與試用數增加 300%",
            "備註": "",
            "更新時間": "2025.07",
            "參考新聞連結": "新聞稿標題",
            "新聞稿": "False",
            "自媒體": "False",
            "Saleskits": "True",
            "口頭說明": "True",
            "演講簡報": "False",
            "官網/ 招募網站": "False",
            "廣告": "False",
        },
        source_row=10,
        captured_date=CAPTURED_DATE,
    )

    assert payload["allowed_exposure_channels"] == ["saleskits", "verbal_briefing"]
    assert payload["claim_status"] == "approved"
    assert payload["metric_updated_date"] == date(2025, 7, 1)
    assert payload["missing_allowed_exposure_channels"] is False


def test_public_metric_all_false_channels_is_not_externally_quotable():
    payload = normalize_public_metric_row(
        {
            "類型": "GMV",
            "指標": "節慶檔期GMV",
            "論述": "節慶檔期 GMV 成長",
            "備註": "",
            "更新時間": "2025.07",
            "參考新聞連結": "",
            "新聞稿": "False",
            "自媒體": "False",
            "Saleskits": "False",
            "口頭說明": "False",
            "演講簡報": "False",
            "官網/ 招募網站": "False",
            "廣告": "False",
        },
        source_row=20,
        captured_date=CAPTURED_DATE,
    )

    assert payload["allowed_exposure_channels"] == []
    assert payload["missing_allowed_exposure_channels"] is True
    assert payload["can_quote_externally"] is False


def test_public_metric_restricted_note_forces_verbal_only_when_required():
    note = "*不可公開實際數字，只能公布區間\n*僅用於口頭說明，不留文字紀錄"
    payload = normalize_public_metric_row(
        {
            "類型": "GMV",
            "指標": "累計總GMV",
            "論述": "累計 GMV 達指定區間",
            "備註": note,
            "更新時間": "2025.07",
            "參考新聞連結": "",
            "新聞稿": "True",
            "自媒體": "True",
            "Saleskits": "True",
            "口頭說明": "True",
            "演講簡報": "True",
            "官網/ 招募網站": "True",
            "廣告": "True",
        },
        source_row=15,
        captured_date=CAPTURED_DATE,
    )

    assert payload["allowed_exposure_channels"] == ["verbal_briefing"]
    assert payload["restricted_note"] == note
    assert payload["missing_allowed_exposure_channels"] is False


def test_no_results_does_not_generate_citation(tmp_path):
    db_path = _build_index(tmp_path, [_public_metric_payload()])

    answer = ask_index(
        "不存在的外部引用資料",
        db_path=db_path,
        filters=SearchFilters(record_type=["merchant_case"]),
    )

    assert answer.citations == []
    assert answer.warnings == []


def test_invalid_record_type_or_channel_returns_validation_error():
    with pytest.raises(ValidationError):
        DocumentMetadata(
            title="Invalid channel",
            source_type="database",
            record_type="public_metric",
            status="published",
            publish_date=CAPTURED_DATE,
            allowed_exposure_channels=["website"],
        )

    with pytest.raises(ValidationError):
        SearchFilters(exposure_channel=["website"])


def test_excel_sheet_plan_marks_governance_tables_as_non_content_index():
    assert EXCEL_INGESTION_PLAN[SHEET_RESTRICTED_CUSTOMERS]["index_role"] == "governance_table"


def _public_metric_payload(
    title="Public metric",
    metric_name="公開指標",
    claim_statement="可公開使用的對外數據",
    allowed_exposure_channels=None,
):
    return {
        "title": title,
        "source_type": "database",
        "record_type": "public_metric",
        "status": "published",
        "publish_date": CAPTURED_DATE,
        "metric_type": "合作夥伴",
        "metric_name": metric_name,
        "claim_statement": claim_statement,
        "claim_status": "approved",
        "data_classification": "public",
        "can_quote_externally": True,
        "allowed_exposure_channels": allowed_exposure_channels or ["saleskits"],
        "source_sheet": "「可公開」對外數據",
        "source_row": 7,
    }


def _content_asset_payload(title, claim_statement):
    return {
        "title": title,
        "source_type": "blog",
        "record_type": "content_asset",
        "status": "published",
        "publish_date": CAPTURED_DATE,
        "topic": ["campaign", "result"],
        "claim_statement": claim_statement,
        "data_classification": "public",
        "can_quote_externally": True,
    }


def _build_index(tmp_path, metadata_payloads):
    documents = []
    for index, payload in enumerate(metadata_payloads):
        metadata = DocumentMetadata(**payload)
        content = " ".join(
            value
            for value in [
                metadata.title,
                metadata.brand_name,
                metadata.metric_name,
                metadata.claim_statement,
                metadata.article_title,
                metadata.video_title,
            ]
            if value
        )
        documents.append(Document(id=f"doc_{index}", metadata=metadata, content=content))

    chunks = chunk_documents(documents, chunk_size=500, overlap=0)
    db_path = tmp_path / "index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunks)
    return db_path


def _write_markdown(path, content):
    path.write_text(content, encoding="utf-8")


def _write_restricted_customers(tmp_path, brand_names):
    import json

    path = tmp_path / "restricted_customers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "record_type": "restricted_customer",
                    "brand_name": brand_name,
                    "data_classification": "restricted",
                    "can_quote_externally": False,
                    "source_sheet": "「不可公開」客戶名單",
                    "source_row": index,
                }
                for index, brand_name in enumerate(brand_names, start=1)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
