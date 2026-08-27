"""Contract tests for the additive query_planning.py changes the faceted-search MVP needs.

Two things are new here, both additive to the existing frozen contract:

1. ``_metadata_matches_constraint`` executes the already-declared ``"in"`` operator for
   ``sales_category_lv1``/``sales_category_lv2`` and ``"contains_any"`` for ``content_tags`` --
   both operators the field registry already allowed but nothing previously executed.
2. ``build_query_plan`` accepts ``preresolved_fields``, so a caller (the Slack modal) can tell the
   free-text parser which taxonomy fields it already decided by another route; the Authority must
   not reopen them, whether by an explicit ``field=value`` mention or by the catalog scan.

Every existing single-value constraint path (``"canonical_exact"``, ``"exact_tag"``, ``"eq"``) is
untouched -- these tests only add coverage for the new list-valued paths.
"""

from datetime import date

import pytest

from marketing_knowledge_agent.models import DocumentMetadata
from marketing_knowledge_agent.query_planning import (
    QueryCatalog,
    QueryConstraint,
    TypedQueryPlan,
    build_query_plan,
    metadata_matches_query_plan,
)
from marketing_knowledge_agent.search_taxonomy import load_search_taxonomy

from test_search_taxonomy import write_taxonomy_workbook


def _metadata(lv2=None, tags=(), year=None):
    return DocumentMetadata(
        title="t",
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 1, 1),
        brand_name="brand",
        sales_category_lv2=lv2,
        content_tags=list(tags),
        interview_year=year,
        data_classification="public",
        can_quote_externally=True,
    )


def _plan(constraint: QueryConstraint) -> TypedQueryPlan:
    return TypedQueryPlan(
        raw_query="",
        normalized_query="",
        query_mode="structured_lookup",
        parsed_terms=[],
        resolved_entities=[],
        constraints=[constraint],
    )


def test_interview_year_in_operator_matches_any_listed_year():
    constraint = QueryConstraint(
        field="interview_year", value=[2023, 2024], normalized_value=[2023, 2024],
        operator="in", match_type="exact", hard_filter=True, source="slack_modal",
    )
    plan = _plan(constraint)

    assert metadata_matches_query_plan(_metadata(year=2023), plan) is True
    assert metadata_matches_query_plan(_metadata(year=2024), plan) is True
    assert metadata_matches_query_plan(_metadata(year=2025), plan) is False


def test_sales_category_lv2_in_operator_matches_any_listed_value():
    constraint = QueryConstraint(
        field="sales_category_lv2", value=["食品/飲料", "男裝"],
        normalized_value=["食品/飲料", "男裝"],
        operator="in", match_type="canonical_exact", hard_filter=True, source="slack_modal",
    )
    plan = _plan(constraint)

    assert metadata_matches_query_plan(_metadata(lv2="食品/飲料"), plan) is True
    assert metadata_matches_query_plan(_metadata(lv2="男裝"), plan) is True
    assert metadata_matches_query_plan(_metadata(lv2="女裝"), plan) is False


def test_content_tags_contains_any_operator_matches_any_listed_tag():
    constraint = QueryConstraint(
        field="content_tags", value=["會員經營", "數位轉型"],
        normalized_value=["會員經營", "數位轉型"],
        operator="contains_any", match_type="exact_tag", hard_filter=True, source="slack_modal",
    )
    plan = _plan(constraint)

    assert metadata_matches_query_plan(_metadata(tags=["會員經營"]), plan) is True
    assert metadata_matches_query_plan(_metadata(tags=["數位轉型", "其他"]), plan) is True
    assert metadata_matches_query_plan(_metadata(tags=["其他"]), plan) is False
    assert metadata_matches_query_plan(_metadata(tags=[]), plan) is False


def test_single_value_operators_are_unaffected_by_the_new_list_handling():
    """The pre-existing scalar contract (``"canonical_exact"``, ``"exact_tag"``) is unchanged."""
    lv2_constraint = QueryConstraint(
        field="sales_category_lv2", value="食品/飲料", normalized_value="食品/飲料",
        operator="canonical_exact", match_type="canonical_exact", hard_filter=True, source="test",
    )
    tag_constraint = QueryConstraint(
        field="content_tags", value="會員經營", normalized_value="會員經營",
        operator="contains_exact_tag", match_type="exact_tag", hard_filter=True, source="test",
    )

    assert metadata_matches_query_plan(_metadata(lv2="食品/飲料"), _plan(lv2_constraint)) is True
    assert metadata_matches_query_plan(_metadata(lv2="男裝"), _plan(lv2_constraint)) is False
    assert metadata_matches_query_plan(_metadata(tags=["會員經營"]), _plan(tag_constraint)) is True
    assert metadata_matches_query_plan(_metadata(tags=["其他"]), _plan(tag_constraint)) is False


# --------------------------------------------------------------------------------------
# preresolved_fields
# --------------------------------------------------------------------------------------

SALES_CATEGORY_ROWS = [
    ["Sales Category LV1", "Sales Category LV1 擴充詞", "Sales Category LV2", "Sales Category LV2 擴充詞"],
    ["居家生活", None, "居家生活相關", None],
    [None, None, "食品/飲料", "美食, 餐飲"],
]
CONTENT_TAG_ROWS = [
    ["內容相關標籤", "內容相關標籤 擴充詞", None],
    ["會員經營", "會員回購", None],
]


@pytest.fixture
def taxonomy(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    sha256 = write_taxonomy_workbook(path, sales_rows=SALES_CATEGORY_ROWS, tag_rows=CONTENT_TAG_ROWS)
    return load_search_taxonomy(workbook_path=path, expected_sha256=sha256)


@pytest.fixture
def catalog():
    return QueryCatalog(
        merchant_names=["三風製麵"],
        merchant_handles=["shanfeng"],
        sales_category_lv2=["食品/飲料"],
        content_tags=["會員經營"],
    )


def test_preresolved_field_suppresses_a_catalog_scan_match(taxonomy, catalog):
    # Without preresolved_fields, "會員經營" in free text binds a content_tags constraint.
    plan = build_query_plan("會員經營案例", catalog, taxonomy=taxonomy)
    assert any(c.field == "content_tags" for c in plan.constraints)

    # With content_tags preresolved (the Slack modal already decided it), the same term must be
    # spent text: no new constraint and no taxonomy-related ambiguity flag or abstain reason.
    # The residual bare text ("案例") still looks like an unresolved lookup on its own terms --
    # that generic "this free text alone did not resolve to anything" signal is unrelated to the
    # taxonomy suppression under test here, and it is structured_search.build_structured_query_plan
    # (see test_structured_search.py) that is responsible for not letting it veto a search that
    # already has real modal-selected hard constraints elsewhere.
    suppressed = build_query_plan(
        "會員經營案例", catalog, taxonomy=taxonomy, preresolved_fields=("content_tags",)
    )
    assert not any(c.field == "content_tags" for c in suppressed.constraints)
    assert not any(flag.startswith("taxonomy_") for flag in suppressed.ambiguity_flags)
    assert suppressed.abstain_reason != "ambiguous_taxonomy_term"
    assert suppressed.abstain_reason != "taxonomy_known_but_not_indexed"


def test_preresolved_field_suppresses_an_explicit_field_mention():
    taxonomy = None  # explicit-field suppression must not even require an Authority to be present
    catalog = QueryCatalog(sales_category_lv2=["食品/飲料"])
    plan = build_query_plan(
        "sales_category_lv2=食品/飲料", catalog, taxonomy=taxonomy, preresolved_fields=("sales_category_lv2",)
    )
    assert not any(c.field == "sales_category_lv2" for c in plan.constraints)


def test_preresolved_field_does_not_suppress_an_ambiguity_spanning_an_undecided_field(taxonomy):
    """An ambiguity naming both a preresolved field and one the modal left untouched still blocks."""
    ambiguous_rows = [
        SALES_CATEGORY_ROWS[0],
        ["居家生活", None, "居家生活相關", None],
        [None, None, "同名詞", "同名詞"],
    ]
    tag_rows = [
        CONTENT_TAG_ROWS[0],
        ["同名詞", None, None],
    ]

    import pathlib

    tmp_dir = pathlib.Path(taxonomy.workbook_path).parent
    path = tmp_dir / "ambiguous.xlsx"
    sha256 = write_taxonomy_workbook(path, sales_rows=ambiguous_rows, tag_rows=tag_rows)
    ambiguous_taxonomy = load_search_taxonomy(workbook_path=path, expected_sha256=sha256)

    catalog = QueryCatalog(sales_category_lv2=["同名詞"], content_tags=["同名詞"])

    # "同名詞" is ambiguous between sales_category_lv2 and content_tags. Preresolving only
    # sales_category_lv2 (content_tags is still undecided) must not clear the ambiguity.
    plan = build_query_plan(
        "同名詞案例", catalog, taxonomy=ambiguous_taxonomy, preresolved_fields=("sales_category_lv2",)
    )
    assert plan.abstain_reason == "ambiguous_taxonomy_term"

    # Preresolving both fields the ambiguity spans does clear the taxonomy ambiguity itself, even
    # though the residual bare text still separately looks like an unresolved lookup on its own --
    # a generic signal, unrelated to this ambiguity, that structured_search.py's merge layer (see
    # test_structured_search.py) is responsible for not letting veto a modal-driven search.
    fully_preresolved = build_query_plan(
        "同名詞案例",
        catalog,
        taxonomy=ambiguous_taxonomy,
        preresolved_fields=("sales_category_lv2", "content_tags"),
    )
    assert not any(flag.startswith("taxonomy_ambiguous_term") for flag in fully_preresolved.ambiguity_flags)
    assert fully_preresolved.abstain_reason != "ambiguous_taxonomy_term"


def test_preresolved_fields_defaults_to_empty_and_changes_nothing(taxonomy, catalog):
    """Omitting the new parameter reproduces the exact pre-existing behaviour."""
    without_param = build_query_plan("會員經營案例", catalog, taxonomy=taxonomy)
    with_empty = build_query_plan("會員經營案例", catalog, taxonomy=taxonomy, preresolved_fields=())

    assert without_param.to_dict() == with_empty.to_dict()
