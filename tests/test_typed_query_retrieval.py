from datetime import date

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchFilters
from marketing_knowledge_agent.pipeline import agent_ask, ask_index, explain_query, search_index
from marketing_knowledge_agent.pipeline import build_index_query_plan
from marketing_knowledge_agent.query_planning import (
    FIELD_REGISTRY,
    QueryCatalog,
    QueryConstraint,
    RUNTIME_SUPPORT_MATRIX,
    TypedQueryPlan,
    build_query_plan,
    metadata_matches_query_plan,
    normalize_query_text,
)


def test_field_registry_keeps_searchable_fields_and_status_semantics_distinct():
    assert FIELD_REGISTRY["merchant_handle"].allowed_operators == ["exact"]
    assert FIELD_REGISTRY["sales_category_lv1"].hard_filter is True
    assert FIELD_REGISTRY["interview_year"].data_type == "integer"
    assert FIELD_REGISTRY["publication_status"].source_field is None
    assert FIELD_REGISTRY["publication_status"].executable is False
    assert FIELD_REGISTRY["publication_status"].value_scope == "asset_level"
    assert FIELD_REGISTRY["interview_status"].executable is False
    assert FIELD_REGISTRY["external_usage_status"].source_field == "can_quote_externally"


def test_runtime_support_matrix_distinguishes_expressible_from_executable_fields():
    supported = {
        "merchant_name",
        "merchant_handle",
        "sales_category_lv1",
        "sales_category_lv2",
        "interview_year",
        "content_tags",
        "asset_type",
        "external_usage_status",
    }
    unsupported = {
        "partner_name",
        "interview_date",
        "published_at",
        "publication_status",
        "interview_status",
        "review_status",
        "asset_url",
    }

    assert all(RUNTIME_SUPPORT_MATRIX[field]["executor_supported"] for field in supported)
    assert all(RUNTIME_SUPPORT_MATRIX[field]["slack_ready"] for field in supported)
    assert all(RUNTIME_SUPPORT_MATRIX[field]["query_plan_expressible"] for field in unsupported)
    assert all(not RUNTIME_SUPPORT_MATRIX[field]["executor_supported"] for field in unsupported)
    assert all(not RUNTIME_SUPPORT_MATRIX[field]["slack_ready"] for field in unsupported)


def test_query_normalization_handles_nfkc_spacing_case_and_handle_prefix():
    assert normalize_query_text("  ＤＡＣＨＵＮ　 ") == "dachun"
    assert normalize_query_text("＠DACHUN") == "@dachun"


def test_exact_brand_plan_is_a_hard_constraint():
    plan = build_query_plan("提供我三風製麵的內容", _catalog())

    constraint = _constraint(plan, "entity_name")
    assert plan.query_mode == "structured_lookup"
    assert constraint.normalized_value == "三風製麵"
    assert constraint.hard_filter is True
    assert plan.fallback_policy == "abstain"


def test_handle_is_tier_zero_exact_and_case_insensitive():
    for query in ("dachun", "@dachun", "DACHUN"):
        plan = build_query_plan(query, _catalog())
        constraint = _constraint(plan, "merchant_handle")
        assert constraint.normalized_value == "dachun"
        assert constraint.match_type == "canonical_exact"
        assert constraint.hard_filter is True


def test_category_uses_canonical_field_not_brand_substring():
    plan = build_query_plan("我們有什麼居家生活品牌相關內容？", _catalog())

    constraint = _constraint(plan, "sales_category_lv1")
    assert constraint.normalized_value == "居家生活"
    assert constraint.operator == "canonical_exact"


def test_interview_year_and_range_are_typed_constraints():
    single = build_query_plan("2025 年採訪的品牌", _catalog())
    ranged = build_query_plan("2024～2025 年採訪", _catalog())

    assert _constraint(single, "interview_year").value == 2025
    assert _constraint(single, "interview_year").operator == "eq"
    assert _constraint(ranged, "interview_year").value == [2024, 2025]
    assert _constraint(ranged, "interview_year").operator == "range"


def test_publication_status_and_asset_type_fail_closed_without_asset_status():
    plan = build_query_plan("已上線的影片", _catalog())

    assert _constraint(plan, "publication_status").support_status == "unsupported"
    assert _constraint(plan, "asset_type").normalized_value == "video"
    assert plan.execution_blocked is True
    assert plan.abstain_reason == "unsupported_hard_constraint"


def test_press_release_is_exposure_channel_not_news_asset():
    plan = build_query_plan("可用於新聞稿的數據", _catalog())

    assert _constraint(plan, "allowed_exposure_channels").normalized_value == "press_release"
    assert not any(item.field == "asset_type" and item.normalized_value == "news" for item in plan.constraints)


def test_unsupported_interview_status_is_ambiguous_not_silently_mapped():
    plan = build_query_plan("已採訪", _catalog())

    assert plan.ambiguity_flags
    assert plan.unsupported_constraints
    assert plan.abstain_reason == "unsupported_hard_constraint"
    assert not any(item.field == "publication_status" for item in plan.constraints)


def test_multiple_constraints_default_to_and():
    plan = build_query_plan("2025 居家生活 已上線 影片", _catalog())

    assert plan.operator == "AND"
    assert {constraint.field for constraint in plan.constraints} >= {
        "interview_year",
        "sales_category_lv1",
        "publication_status",
        "asset_type",
    }
    assert plan.execution_blocked is True


def test_query_plan_round_trips_without_losing_typed_constraints():
    plan = build_query_plan("2025 居家生活 已上線 影片", _catalog())

    restored = TypedQueryPlan.from_dict(plan.to_dict())

    assert restored.to_dict() == plan.to_dict()


def test_conflicting_years_without_or_are_ambiguous():
    plan = build_query_plan("2024＋2025 採訪", _catalog())

    assert "conflicting_interview_years" in plan.ambiguity_flags
    assert plan.abstain_reason == "conflicting_constraints"


def test_same_merchant_and_partner_name_preserves_entity_ambiguity():
    catalog = QueryCatalog(
        merchant_names=["Shared Name"],
        partner_names=["Shared Name"],
    )

    plan = build_query_plan("Shared Name", catalog)

    assert "entity_type_ambiguous" in plan.ambiguity_flags
    assert plan.abstain_reason == "ambiguous_entity_type"


def test_unknown_field_and_operator_fail_closed():
    metadata, _ = _record("Example", "example", "美食", 2025, article="Example article")
    unknown_field = _manual_plan("review_status", "pending", "exact")
    unknown_operator = _manual_plan("interview_year", 2025, "contains")

    assert unknown_field.execution_blocked is True
    assert [item.field for item in unknown_field.unsupported_constraints] == ["review_status"]
    assert metadata_matches_query_plan(metadata, unknown_field) is False
    assert unknown_operator.execution_blocked is True
    assert [item.field for item in unknown_operator.invalid_constraints] == ["interview_year"]
    assert metadata_matches_query_plan(metadata, unknown_operator) is False


@pytest.mark.parametrize(
    ("query", "unsupported_field", "supported_field"),
    [
        ("某夥伴名稱＋影片", "partner_name", "asset_type"),
        ("待審核＋影片", "review_status", "asset_type"),
        ("已上線的影片", "publication_status", "asset_type"),
        ("review_status=pending＋asset_type=video", "review_status", "asset_type"),
        ("asset_url=https://example.com＋asset_type=article", "asset_url", "asset_type"),
        ("published_at=2025-07-01＋asset_type=video", "published_at", "asset_type"),
    ],
)
def test_unsupported_and_supported_constraints_block_the_whole_plan(query, unsupported_field, supported_field):
    plan = build_query_plan(query, _catalog())

    assert _constraint(plan, unsupported_field).support_status == "unsupported"
    assert _constraint(plan, supported_field).support_status == "supported"
    assert plan.execution_blocked is True
    assert plan.abstain_reason == "unsupported_hard_constraint"
    assert plan.ambiguity_flags
    assert plan.parser_warnings


@pytest.mark.parametrize("query", ["2025-07-01", "2025/07/01", "2025.07.01"])
def test_full_date_is_unsupported_and_never_parsed_as_interview_year(query):
    plan = build_query_plan(query, _catalog())

    assert not any(item.field == "interview_year" for item in plan.constraints)
    assert _constraint(plan, "interview_date").support_status == "unsupported"
    assert plan.execution_blocked is True


def test_published_date_is_unsupported_and_never_downgraded_to_interview_year():
    plan = build_query_plan("2025-07-01 上線的影片", _catalog())

    assert not any(item.field == "interview_year" for item in plan.constraints)
    assert _constraint(plan, "published_at").support_status == "unsupported"
    assert _constraint(plan, "asset_type").support_status == "supported"
    assert plan.execution_blocked is True


def test_unsupported_messages_survive_explicit_filters(tmp_path):
    db_path = _build_index(tmp_path)
    plan = build_index_query_plan(
        "待審核內容",
        db_path,
        SearchFilters(record_type=["merchant_case"]),
    )

    assert plan.unsupported_constraints
    assert plan.ambiguity_flags
    assert plan.parser_warnings
    assert plan.execution_blocked is True
    assert plan.abstain_reason == "unsupported_hard_constraint"


def test_exact_brand_search_does_not_fill_with_similar_brands(tmp_path):
    db_path = _build_index(tmp_path)

    results = search_index("莉朵花藝", db_path, limit=5)

    assert [result.chunk.metadata.brand_name for result in results] == ["莉朵花藝"]


def test_handle_search_returns_only_exact_merchant(tmp_path):
    db_path = _build_index(tmp_path)

    results = search_index("dachun", db_path, limit=5)

    assert results
    assert {result.chunk.metadata.brand_name for result in results} == {"大春煉皂"}
    assert {result.chunk.metadata.merchant_handle for result in results} == {"dachun"}


def test_brand_question_never_adds_other_merchants_to_fill_top_k(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("提供我三風製麵的內容", db_path, limit=5)

    assert answer.structured_result is not None
    assert {entity.entity_name for entity in answer.structured_result.matched_entities} == {"三風製麵"}
    assert "OTHER_BRAND_MARK" not in answer.answer
    assert all("三風製麵" in citation.title for citation in answer.citations)


def test_category_search_uses_metadata_only(tmp_path):
    db_path = _build_index(tmp_path)

    results = search_index("我們有什麼居家生活品牌相關內容？", db_path, limit=10)

    assert results
    brands = {result.chunk.metadata.brand_name for result in results}
    assert "生活倉庫" in brands
    assert "STANCAVE" not in brands
    assert "藥師健生活" not in brands
    assert all(
        result.chunk.metadata.sales_category_lv1 == "居家生活"
        or result.chunk.metadata.sales_category_lv2 == "居家生活"
        for result in results
    )


def test_year_filter_uses_interview_year_not_publish_date(tmp_path):
    db_path = _build_index(tmp_path)

    results = search_index("2025 年採訪的品牌", db_path, limit=20)

    assert results
    assert all(result.chunk.metadata.interview_year == 2025 for result in results)
    assert "Published In 2025 Only" not in {result.chunk.metadata.brand_name for result in results}


def test_handle_and_asset_type_are_intersected(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("dachun Podcast", db_path, limit=10)

    assets = answer.structured_result.matched_entities[0].assets
    assert [asset.asset_type for asset in assets] == ["podcast"]
    assert all(entity.entity_name == "大春煉皂" for entity in answer.structured_result.matched_entities)


def test_entity_and_exact_content_tag_are_intersected(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("三風製麵＋數位轉型", db_path, limit=10)

    assert answer.citations
    assert {entity.entity_name for entity in answer.structured_result.matched_entities} == {"三風製麵"}
    assert {item["field"] for item in answer.query_plan["hard_filters"]} >= {"entity_name", "content_tags"}


def test_category_and_article_return_only_article_assets(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("居家生活＋文章", db_path, limit=10)

    assets = [asset for entity in answer.structured_result.matched_entities for asset in entity.assets]
    assert assets
    assert all(asset.asset_type == "article" for asset in assets)
    assert all(entity.sales_category_lv1 == "居家生活" for entity in answer.structured_result.matched_entities)


def test_exact_asset_title_returns_only_that_asset_type(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("三風製麵數位轉型影片", db_path, limit=10)

    assets = [asset for entity in answer.structured_result.matched_entities for asset in entity.assets]
    assert len(assets) == 1
    assert assets[0].asset_type == "video"
    assert _constraint(TypedQueryPlan.from_dict(answer.query_plan), "asset_title").hard_filter is True


def test_zero_intersection_does_not_relax_to_or(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("2025 居家生活 Podcast", db_path, limit=10)

    assert answer.citations == []
    assert answer.structured_result.total_assets == 0
    assert "找不到同時符合" in answer.answer


@pytest.mark.parametrize(
    "query",
    [
        "某夥伴名稱＋影片",
        "待審核＋影片",
        "已上線的影片",
        "review_status=pending＋asset_type=video",
        "asset_url=https://example.com＋asset_type=article",
        "published_at=2025-07-01＋asset_type=video",
    ],
)
def test_unsupported_constraint_returns_no_partial_results_or_citations(tmp_path, query):
    db_path = _build_index(tmp_path)

    answer = ask_index(query, db_path, limit=20)

    assert answer.structured_result is not None
    assert answer.structured_result.abstained is True
    assert answer.structured_result.total_entities == 0
    assert answer.structured_result.total_assets == 0
    assert answer.structured_result.unsupported_constraints
    assert answer.citations == []
    assert answer.query_plan["execution_blocked"] is True
    assert answer.query_plan["abstain_reason"] == "unsupported_hard_constraint"


def test_unknown_bare_entity_abstains_instead_of_returning_similar_brand(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("不存在花藝品牌", db_path, limit=5)

    assert answer.citations == []
    assert "莉朵花藝" not in answer.answer
    assert answer.structured_result.abstained is True


def test_structured_renderer_omits_empty_asset_sections_and_preserves_traceability(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("提供我三風製麵的內容", db_path, limit=5)

    assert "文章" in answer.answer
    assert "影片" in answer.answer
    assert "Podcast" not in answer.answer
    assert "新聞" not in answer.answer
    assert "連結：資料未提供" in answer.answer
    assert "資料來源：商家夥伴案例資料庫 r8" in answer.answer
    assert all(citation.source_sheet and citation.source_row for citation in answer.citations)


def test_parent_record_status_is_not_copied_to_structured_assets(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("提供我三風製麵的內容", db_path, limit=5)

    assets = [asset for entity in answer.structured_result.matched_entities for asset in entity.assets]
    assert assets
    assert all(asset.publication_status is None for asset in assets)
    assert "狀態：資料未提供" in answer.answer


def test_agentic_path_reuses_same_hard_constraints(tmp_path):
    db_path = _build_index(tmp_path)

    answer = agent_ask("請整理三風製麵有哪些內容", db_path, limit=5)

    assert answer.generated.structured_result is not None
    assert {entity.entity_name for entity in answer.generated.structured_result.matched_entities} == {"三風製麵"}
    assert "OTHER_BRAND_MARK" not in answer.answer


def test_semantic_question_keeps_entity_as_hard_candidate_scope(tmp_path):
    db_path = _build_index(tmp_path)

    answer = ask_index("三風製麵如何提升業績", db_path, limit=5)

    assert answer.query_plan["query_mode"] == "semantic_question"
    assert answer.citations
    assert all("三風製麵" in citation.title for citation in answer.citations)
    assert "OTHER_BRAND_MARK" not in answer.answer


def test_explain_query_reports_safe_counts_and_no_content(tmp_path):
    db_path = _build_index(tmp_path)

    explanation = explain_query("2025 居家生活 已上線 影片", db_path)

    assert explanation["query_plan"]["operator"] == "AND"
    assert explanation["candidate_count_before_filtering"] > explanation["candidate_count_after_filtering"]
    assert "document_content" not in explanation
    assert "source_path" not in str(explanation)


def test_explain_query_reports_unsupported_constraint_without_retrieval(tmp_path):
    db_path = _build_index(tmp_path)

    explanation = explain_query("已上線的影片", db_path)

    assert explanation["execution_blocked"] is True
    assert [item["field"] for item in explanation["unsupported_constraints"]] == ["publication_status"]
    assert explanation["candidate_count_after_filtering"] == 0
    assert explanation["final_entity_count"] == 0
    assert explanation["final_asset_count"] == 0
    assert explanation["abstain_reason"] == "unsupported_hard_constraint"


def test_explain_query_cli_outputs_plan_without_content_or_source_path(tmp_path, capsys):
    db_path = _build_index(tmp_path)

    exit_code = main(
        [
            "explain-query",
            "dachun",
            "--db",
            str(db_path),
            "--restricted-customers",
            str(tmp_path / "missing-denylist.json"),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"field": "merchant_handle"' in output
    assert "source_path" not in output
    assert "大春煉皂文章" not in output


def test_explain_query_redacts_restricted_query_before_planning(tmp_path):
    db_path = _build_index(tmp_path)
    restricted_term = "Restricted Synthetic Identity"
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_term)])

    explanation = explain_query(restricted_term, db_path, governance_index=governance_index)

    assert restricted_term not in str(explanation)
    assert explanation["abstain_reason"] == "restricted_query"
    assert explanation["candidate_count_before_filtering"] == 0


def test_structured_asset_title_is_governed_before_contract_output(tmp_path):
    restricted_term = "Restricted Synthetic Asset"
    metadata, content = _record(
        "Clean Merchant",
        "clean-merchant",
        "居家生活",
        2025,
        article="Clean article title",
        video=f"{restricted_term} video title",
    )
    document = Document(id="doc-clean", metadata=metadata, content=content)
    db_path = tmp_path / "governed-structured.sqlite"
    SQLiteIndex(db_path).rebuild([document], chunk_documents([document]))
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_term)])

    answer = ask_index("Clean Merchant", db_path, governance_index=governance_index)

    assert restricted_term not in answer.answer
    assert restricted_term not in str(answer.structured_result)
    assert [asset.asset_type for asset in answer.structured_result.matched_entities[0].assets] == ["article"]
    assert len(answer.citations) == 1
    assert any("結構化資產" in warning for warning in answer.warnings)


def _constraint(plan, field):
    return next(item for item in plan.constraints if item.field == field)


def _manual_plan(field, value, operator):
    constraint = QueryConstraint(
        field=field,
        value=value,
        normalized_value=value,
        operator=operator,
        match_type="exact",
        hard_filter=True,
        source="test",
    )
    return TypedQueryPlan(
        raw_query=f"{field}={value}",
        normalized_query=f"{field}={value}",
        query_mode="structured_lookup",
        parsed_terms=[str(value)],
        resolved_entities=[],
        constraints=[constraint],
    )


def _catalog():
    return QueryCatalog(
        merchant_names=["三風製麵", "大春煉皂", "莉朵花藝", "藥師健生活"],
        partner_names=[],
        merchant_handles=["shanfeng", "dachun", "lido"],
        sales_category_lv1=["美食", "居家生活", "流行服飾", "醫療與保健"],
        sales_category_lv2=["食品/飲料", "男裝", "養生/保健"],
        content_tags=["數位轉型", "會員經營"],
    )


def _build_index(tmp_path):
    records = [
        _record("莉朵花藝", "lido", "居家生活", 2025, article="莉朵花藝品牌故事"),
        _record("芙拉花藝", "flora", "其他", 2025, article="芙拉花藝案例"),
        _record("浪花花藝", "waveflower", "其他", 2024, article="浪花花藝案例"),
        _record(
            "大春煉皂",
            "dachun",
            "美妝保養＆個人護理",
            2024,
            article="大春煉皂文章",
            video="大春煉皂影片",
            podcast="大春煉皂 Podcast",
        ),
        _record(
            "三風製麵",
            "shanfeng",
            "美食",
            2026,
            article="三風製麵數位轉型文章",
            video="三風製麵數位轉型影片",
            source_row=8,
        ),
        _record("生活倉庫", "life-store", "居家生活", 2025, article="收納用品文章"),
        _record("STANCAVE", "stancave", "流行服飾", 2025, article="生活風格男裝"),
        _record("藥師健生活", "phargoods", "醫療與保健", 2025, article="健康生活文章"),
        _record(
            "Published In 2025 Only",
            "published-only",
            "居家生活",
            2024,
            article="2025 published article",
            publish_date=date(2025, 6, 1),
        ),
        _record("Other Brand", "other", "居家生活", 2024, article="OTHER_BRAND_MARK"),
    ]
    documents = []
    for index, (metadata, content) in enumerate(records, start=1):
        documents.append(Document(id=f"doc-{index}", metadata=metadata, content=content))
    db_path = tmp_path / "typed.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _record(
    brand_name,
    handle,
    category,
    interview_year,
    *,
    article=None,
    video=None,
    podcast=None,
    news=None,
    source_row=1,
    publish_date=date(2026, 7, 1),
):
    title = article or video or podcast or news or brand_name
    metadata = DocumentMetadata(
        title=title,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=publish_date,
        source_path=f"商家夥伴案例資料庫:{source_row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=source_row,
        brand_name=brand_name,
        merchant_handle=handle,
        merchant_status="現有商家",
        interview_year=interview_year,
        sales_category_lv1=category,
        sales_category_lv2="食品/飲料" if category == "美食" else "其他",
        content_tags=["數位轉型"] if brand_name == "三風製麵" else [],
        article_title=article,
        video_title=video,
        podcast_title=podcast,
        news_title=news,
        data_classification="public",
        can_quote_externally=True,
    )
    content = "\n".join(value for value in [title, article, video, podcast, news] if value)
    return metadata, content
