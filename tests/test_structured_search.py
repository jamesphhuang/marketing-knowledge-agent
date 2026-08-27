"""Contract tests for structured_search.py: request validation, plan building and execution.

Covers the semantics the spec calls load-bearing: OR within one modal field, AND across fields, a
modal-selected field that free text can never override or widen, deterministic ordering for a pure
structured browse (no free text), hard filters applied before any lexical/semantic scoring, and that
restricted/non-retrievable/pending records never surface through either execution path.
"""

import json
from datetime import date

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.query_planning import QueryCatalog
from marketing_knowledge_agent.search_facets import build_facet_catalog
from marketing_knowledge_agent.search_taxonomy import load_search_taxonomy
from marketing_knowledge_agent.slack_presentation import format_structured_slack_reply
from marketing_knowledge_agent.structured_search import (
    StaleFacetCatalogError,
    StructuredSearchRequest,
    StructuredSearchValidationError,
    build_structured_query_plan,
    execute_structured_search,
    validate_structured_search_request,
)

from test_search_taxonomy import write_taxonomy_workbook


SALES_CATEGORY_ROWS = [
    ["Sales Category LV1", "Sales Category LV1 擴充詞", "Sales Category LV2", "Sales Category LV2 擴充詞"],
    ["居家生活", None, "居家生活相關", None],
    [None, None, "食品/飲料", "美食, 餐飲"],
    [None, None, "男裝", None],
]
CONTENT_TAG_ROWS = [
    ["內容相關標籤", "內容相關標籤 擴充詞", None],
    ["會員經營", "會員回購", None],
    ["數位轉型", None, None],
]


@pytest.fixture
def taxonomy(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    sha256 = write_taxonomy_workbook(path, sales_rows=SALES_CATEGORY_ROWS, tag_rows=CONTENT_TAG_ROWS)
    return load_search_taxonomy(workbook_path=path, expected_sha256=sha256)


def _metadata(
    brand_name,
    handle,
    lv2,
    tags,
    year,
    *,
    article=None,
    status="published",
    can_quote_externally=True,
    record_type="merchant_case",
    source_row=1,
):
    metadata = DocumentMetadata(
        title=article or brand_name,
        source_type="database",
        record_type=record_type,
        status=status,
        publish_date=date(2026, 1, 1),
        source_path=f"商家夥伴案例資料庫:{source_row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=source_row,
        brand_name=brand_name,
        merchant_handle=handle,
        merchant_status="現有商家",
        interview_year=year,
        sales_category_lv2=lv2,
        content_tags=list(tags),
        article_title=article or brand_name,
        data_classification="public",
        can_quote_externally=can_quote_externally,
    )
    content = article or brand_name
    return metadata, content


def _build_index(tmp_path, records, name="content_index.sqlite"):
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / name
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _restricted_customers_path(tmp_path, brand_names):
    path = tmp_path / "restricted_customers.json"
    path.write_text(json.dumps([{"brand_name": name} for name in brand_names]), encoding="utf-8")
    return path


def _records():
    return [
        _metadata("莉朵花藝", "lido", "居家生活相關", ["會員經營"], 2025, source_row=1),
        _metadata("大春煉皂", "dachun", "食品/飲料", ["數位轉型", "會員經營"], 2024, source_row=2),
        _metadata("三風製麵", "shanfeng", "食品/飲料", ["數位轉型"], 2023, source_row=3),
        _metadata("Draft Brand", "draftbrand", "男裝", [], 2022, status="draft", source_row=4),
        _metadata(
            "Restricted Brand", "restrictedbrand", "男裝", ["會員經營"], 2021, source_row=5
        ),
    ]


@pytest.fixture
def facet_catalog(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _records())
    restricted_path = _restricted_customers_path(tmp_path, ["Restricted Brand"])
    return build_facet_catalog(db_path, taxonomy, restricted_customers_path=restricted_path), db_path, restricted_path


@pytest.fixture
def query_catalog():
    return QueryCatalog(
        merchant_names=["莉朵花藝", "大春煉皂", "三風製麵"],
        merchant_handles=["lido", "dachun", "shanfeng"],
        sales_category_lv2=["居家生活相關", "食品/飲料"],
        content_tags=["會員經營", "數位轉型"],
    )


# --------------------------------------------------------------------------------------
# validate_structured_search_request
# --------------------------------------------------------------------------------------


def test_stale_catalog_version_is_refused(facet_catalog):
    catalog, _db_path, _restricted_path = facet_catalog
    request = StructuredSearchRequest(interview_years=(2024,), catalog_version="stale-version")

    with pytest.raises(StaleFacetCatalogError):
        validate_structured_search_request(request, catalog)


def test_all_fields_empty_is_refused(facet_catalog):
    catalog, _db_path, _restricted_path = facet_catalog
    request = StructuredSearchRequest(catalog_version=catalog.catalog_version)

    with pytest.raises(StructuredSearchValidationError):
        validate_structured_search_request(request, catalog)


def test_unknown_option_values_are_refused(facet_catalog):
    catalog, _db_path, _restricted_path = facet_catalog
    for request in (
        StructuredSearchRequest(interview_years=(1999,), catalog_version=catalog.catalog_version),
        StructuredSearchRequest(sales_category_lv2=("不存在的類別",), catalog_version=catalog.catalog_version),
        StructuredSearchRequest(content_tags=("不存在的標籤",), catalog_version=catalog.catalog_version),
        # Excluded by governance -- must be just as unavailable as one the Authority never stated.
        StructuredSearchRequest(sales_category_lv2=("男裝",), catalog_version=catalog.catalog_version),
    ):
        with pytest.raises(StructuredSearchValidationError):
            validate_structured_search_request(request, catalog)


def test_more_than_three_selections_in_one_field_is_refused(facet_catalog):
    catalog, _db_path, _restricted_path = facet_catalog
    request = StructuredSearchRequest(
        interview_years=(2025, 2024, 2023, 2022), catalog_version=catalog.catalog_version
    )
    with pytest.raises(StructuredSearchValidationError):
        validate_structured_search_request(request, catalog)


def test_a_valid_single_field_selection_passes(facet_catalog):
    catalog, _db_path, _restricted_path = facet_catalog
    request = StructuredSearchRequest(interview_years=(2024,), catalog_version=catalog.catalog_version)
    validate_structured_search_request(request, catalog)  # must not raise


# --------------------------------------------------------------------------------------
# build_structured_query_plan: AND/OR semantics and free-text non-override
# --------------------------------------------------------------------------------------


def test_multiple_values_in_one_field_are_ored(query_catalog, taxonomy):
    request = StructuredSearchRequest(interview_years=(2024, 2023))
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    constraint = next(c for c in plan.constraints if c.field == "interview_year")
    assert constraint.operator == "in"
    assert set(constraint.value) == {2023, 2024}


def test_different_fields_are_anded(query_catalog, taxonomy):
    request = StructuredSearchRequest(interview_years=(2024,), sales_category_lv2=("食品/飲料",))
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    fields = {c.field for c in plan.constraints}
    assert fields == {"interview_year", "sales_category_lv2"}
    assert plan.operator == "AND"


def test_free_text_cannot_override_or_widen_a_modal_selected_field(query_catalog, taxonomy):
    request = StructuredSearchRequest(
        sales_category_lv2=("居家生活相關",), free_text="sales_category_lv2=食品/飲料"
    )
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    lv2_constraints = [c for c in plan.constraints if c.field == "sales_category_lv2"]
    assert len(lv2_constraints) == 1
    assert lv2_constraints[0].value == ["居家生活相關"]
    assert lv2_constraints[0].source == "slack_modal"


def test_free_text_still_parses_fields_the_modal_left_untouched(query_catalog, taxonomy):
    request = StructuredSearchRequest(interview_years=(2024,), free_text="大春煉皂")
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    assert any(c.field == "entity_name" and c.value == "大春煉皂" for c in plan.constraints)


def test_free_text_alone_is_a_valid_structured_search(query_catalog, taxonomy):
    request = StructuredSearchRequest(free_text="大春煉皂")
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    assert plan.query_mode == "structured_lookup"
    assert any(c.field == "entity_name" for c in plan.constraints)


def test_unrelated_free_text_does_not_veto_a_modal_driven_search(query_catalog, taxonomy):
    """Free text that resolves to nothing must not block a search that already has real facets."""
    request = StructuredSearchRequest(
        interview_years=(2024,), free_text="這段文字不對應任何已知欄位或名稱"
    )
    plan = build_structured_query_plan(request, query_catalog, taxonomy)

    assert plan.abstain_reason is None
    assert plan.execution_blocked is False


# --------------------------------------------------------------------------------------
# execute_structured_search
# --------------------------------------------------------------------------------------


def test_browse_mode_orders_newest_interview_year_first(facet_catalog):
    catalog, db_path, restricted_path = facet_catalog
    request = StructuredSearchRequest(
        content_tags=("會員經營",), catalog_version=catalog.catalog_version
    )
    validate_structured_search_request(request, catalog)

    answer = execute_structured_search(
        request, db_path=db_path, taxonomy=None, restricted_customers_path=restricted_path
    )

    years = [entity.interview_year for entity in answer.structured_result.matched_entities]
    assert years == sorted(years, reverse=True)
    assert years == [2025, 2024]  # 莉朵花藝 (2025) and 大春煉皂 (2024) both carry 會員經營


def test_restricted_customer_is_excluded_from_browse_results(facet_catalog):
    """The execution layer's own governance check, independent of facet-catalog validation.

    "會員經營" is carried by three records: two eligible (莉朵花藝, 大春煉皂) and one denylisted
    (Restricted Brand). A content-tag-only request has no reason to notice the denylisted one was
    ever there, so this proves ``execute_structured_search`` itself re-applies the denylist rather
    than relying solely on the catalog only ever offering "safe" option values.
    """
    catalog, db_path, restricted_path = facet_catalog
    request = StructuredSearchRequest(
        content_tags=("會員經營",), catalog_version=catalog.catalog_version
    )
    validate_structured_search_request(request, catalog)

    answer = execute_structured_search(
        request, db_path=db_path, taxonomy=None, restricted_customers_path=restricted_path
    )

    names = [entity.entity_name for entity in answer.structured_result.matched_entities]
    assert "Restricted Brand" not in names
    assert set(names) == {"莉朵花藝", "大春煉皂"}


def test_free_text_search_hard_filters_before_scoring(facet_catalog, query_catalog, taxonomy):
    catalog, db_path, restricted_path = facet_catalog
    # "大春煉皂" is a strong lexical match for the free text, but it is not in 2025 -- the hard
    # filter must exclude it even though it would otherwise rank well.
    request = StructuredSearchRequest(
        interview_years=(2025,), free_text="大春煉皂", catalog_version=catalog.catalog_version
    )
    validate_structured_search_request(request, catalog)

    answer = execute_structured_search(
        request, db_path=db_path, taxonomy=taxonomy, restricted_customers_path=restricted_path
    )

    names = [entity.entity_name for entity in answer.structured_result.matched_entities]
    assert "大春煉皂" not in names


def test_zero_result_reports_applied_filters_without_relaxing_them(facet_catalog):
    catalog, db_path, restricted_path = facet_catalog
    request = StructuredSearchRequest(
        interview_years=(2025,),
        sales_category_lv2=("食品/飲料",),
        catalog_version=catalog.catalog_version,
    )
    validate_structured_search_request(request, catalog)

    answer = execute_structured_search(
        request, db_path=db_path, taxonomy=None, restricted_customers_path=restricted_path
    )

    # No document is both 2025 and 食品/飲料 in this fixture.
    assert answer.structured_result.total_entities == 0
    applied_fields = {c["field"] for c in answer.structured_result.query_plan["hard_filters"]}
    assert applied_fields == {"interview_year", "sales_category_lv2"}


def test_slack_reply_renders_multi_value_filters_without_python_list_syntax(facet_catalog):
    """The Slack-facing "已套用搜尋條件" line must read "2024、2023", never "[2024, 2023]"."""
    catalog, db_path, restricted_path = facet_catalog
    request = StructuredSearchRequest(
        interview_years=(2024, 2023),
        sales_category_lv2=("食品/飲料",),
        catalog_version=catalog.catalog_version,
    )
    validate_structured_search_request(request, catalog)

    answer = execute_structured_search(
        request, db_path=db_path, taxonomy=None, restricted_customers_path=restricted_path
    )
    reply = format_structured_slack_reply(answer)
    conditions_line = reply.splitlines()[0]

    assert "[" not in conditions_line and "]" not in conditions_line
    assert "2024、2023" in conditions_line or "2023、2024" in conditions_line


def test_content_index_and_denylist_are_untouched_by_execution(facet_catalog):
    catalog, db_path, restricted_path = facet_catalog
    before_db = db_path.read_bytes()
    before_restricted = restricted_path.read_bytes()
    request = StructuredSearchRequest(
        interview_years=(2024,), catalog_version=catalog.catalog_version
    )

    execute_structured_search(request, db_path=db_path, taxonomy=None, restricted_customers_path=restricted_path)

    assert db_path.read_bytes() == before_db
    assert restricted_path.read_bytes() == before_restricted
