"""Targeted tests for the pinned, read-only Search Taxonomy Authority and its planner integration.

The fixture workbook is synthetic but deliberately hostile in the same ways the formal 2026-08-21
workbook is: a canonical value carrying trailing whitespace, a full-width character that only NFKC
resolves, one term naming two canonical values inside a field, one naming values in two fields, an
LV2 column that continues past the end of the LV1 column, a canonical name containing an ideographic
comma, a trailing separator that yields an empty term, a blank-header third column, and a taxonomy
term that is also a merchant brand.
"""

import hashlib
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchFilters
from marketing_knowledge_agent.pipeline import (
    agent_ask,
    ask_index,
    build_index_query_plan,
    explain_query,
    search_index,
)
from marketing_knowledge_agent.query_planning import (
    CATEGORY_ALIASES,
    TAXONOMY_FIELDS,
    QueryCatalog,
    allow_semantic_fallback,
    build_query_plan,
)
from marketing_knowledge_agent.search_taxonomy import (
    SHEET_CONTENT_TAGS,
    SHEET_SALES_CATEGORY,
    SearchTaxonomyError,
    TaxonomyResolutionStatus,
    load_search_taxonomy,
)


SALES_CATEGORY_HEADERS = [
    "Sales Category LV1",
    "Sales Category LV1 擴充詞",
    "Sales Category LV2",
    "Sales Category LV2 擴充詞",
]
CONTENT_TAG_HEADERS = ["內容相關標籤", "內容相關標籤 擴充詞", None]

SALES_CATEGORY_ROWS = [
    SALES_CATEGORY_HEADERS,
    # LV1 canonical keeps its trailing space; "居家生活" is that canonical normalized *and* an LV2
    # expansion term, so the bare word names two fields.
    ["居家生活 ", "家居生活, 生活百貨", "居家生活相關", "居家生活, 家居雜貨"],
    # The same canonical name at both levels, differing only by trailing space and case.
    ["Grocery ", "FMCG, 雜貨", "Grocery", "超市"],
    # Full-width solidus: only NFKC makes this the value the index actually carries.
    ["美食", "食品", "食品／飲料", "飲料"],
    # LV2 continues past the end of LV1; "內著" names two LV2 canonicals.
    [None, None, "女裝", "內著, 家居服"],
    [None, None, "男裝", "內著, 家居服"],
    # Known to the Authority, absent from the formal index.
    ["未進索引大類", "只有權威沒有索引", None, None],
    # A taxonomy term that is also a merchant brand.
    ["三風製麵", "製麵廠", None, None],
]

CONTENT_TAG_ROWS = [
    CONTENT_TAG_HEADERS,
    # Trailing separator yields an empty term; the third column holds a reference URL under a blank
    # header and is outside this contract.
    ["團購解決方案", "團購, 開團,", "https://example.invalid/ref"],
    # The ideographic comma is a term character here, not a separator.
    ["直播串接（LINE、FB 等）", "直播串接, LINE 直播", None],
    ["未進索引標籤", "沒有索引的標籤", None],
]


def write_taxonomy_workbook(path: Path, sales_rows=None, tag_rows=None) -> str:
    _write_xlsx(
        path,
        {
            SHEET_SALES_CATEGORY: SALES_CATEGORY_ROWS if sales_rows is None else sales_rows,
            SHEET_CONTENT_TAGS: CONTENT_TAG_ROWS if tag_rows is None else tag_rows,
        },
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def taxonomy(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    return load_search_taxonomy(
        workbook_path=path, expected_sha256=write_taxonomy_workbook(path)
    )


@pytest.fixture
def catalog():
    """A formal index that carries some of the Authority's vocabulary, but not all of it."""
    return QueryCatalog(
        merchant_names=["三風製麵"],
        merchant_handles=["sanfong"],
        sales_category_lv1=["居家生活", "美食"],
        sales_category_lv2=["女裝", "食品/飲料", "居家生活相關"],
        # The last tag exists in the index but is absent from the Authority.
        content_tags=["團購解決方案", "直播串接（LINE、FB 等）", "索引獨有標籤"],
    )


# --------------------------------------------------------------------------------------
# Authority loader
# --------------------------------------------------------------------------------------


def test_loader_accepts_only_the_pinned_workbook_and_leaves_it_untouched(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(path)
    before = path.read_bytes()

    loaded = load_search_taxonomy(workbook_path=path, expected_sha256=expected)

    assert loaded.workbook_sha256 == expected
    assert path.read_bytes() == before


def test_loader_refuses_a_workbook_that_does_not_match_the_pin(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    write_taxonomy_workbook(path)
    with pytest.raises(SearchTaxonomyError, match="lineage mismatch"):
        load_search_taxonomy(workbook_path=path, expected_sha256="0" * 64)


def test_loader_refuses_a_missing_workbook(tmp_path):
    with pytest.raises(SearchTaxonomyError, match="does not exist"):
        load_search_taxonomy(
            workbook_path=tmp_path / "absent.xlsx", expected_sha256="0" * 64
        )


def test_loader_refuses_a_malformed_expected_hash(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    write_taxonomy_workbook(path)
    with pytest.raises(SearchTaxonomyError, match="64 lowercase hex"):
        load_search_taxonomy(workbook_path=path, expected_sha256="not-a-hash")


def test_loader_refuses_a_symlinked_workbook(tmp_path):
    real = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(real)
    link = tmp_path / "link.xlsx"
    link.symlink_to(real)
    with pytest.raises(SearchTaxonomyError, match="symlink"):
        load_search_taxonomy(workbook_path=link, expected_sha256=expected)


def test_loader_refuses_a_renamed_header(tmp_path):
    rows = [list(SALES_CATEGORY_HEADERS), *SALES_CATEGORY_ROWS[1:]]
    rows[0][1] = "Sales Category LV1 同義詞"
    path = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(path, sales_rows=rows)
    with pytest.raises(SearchTaxonomyError, match="header mismatch"):
        load_search_taxonomy(workbook_path=path, expected_sha256=expected)


def test_loader_refuses_an_extra_named_column(tmp_path):
    """A blank trailing header is documented and trimmed; a *named* one is a schema change."""
    rows = [[*CONTENT_TAG_HEADERS[:2], "參考連結"], *CONTENT_TAG_ROWS[1:]]
    path = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(path, tag_rows=rows)
    with pytest.raises(SearchTaxonomyError, match="header mismatch"):
        load_search_taxonomy(workbook_path=path, expected_sha256=expected)


def test_loader_refuses_a_missing_sheet(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    _write_xlsx(path, {SHEET_SALES_CATEGORY: SALES_CATEGORY_ROWS})
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SearchTaxonomyError, match="no sheet named"):
        load_search_taxonomy(workbook_path=path, expected_sha256=expected)


def test_loader_refuses_expansion_terms_with_no_canonical_owner(tmp_path):
    rows = [*SALES_CATEGORY_ROWS, [None, "無主的擴充詞", None, None]]
    path = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(path, sales_rows=rows)
    with pytest.raises(SearchTaxonomyError, match="no canonical value"):
        load_search_taxonomy(workbook_path=path, expected_sha256=expected)


def test_loader_refuses_a_repeated_canonical_value(tmp_path):
    rows = [*SALES_CATEGORY_ROWS, ["美食", "重複的大類", None, None]]
    path = tmp_path / "taxonomy.xlsx"
    expected = write_taxonomy_workbook(path, sales_rows=rows)
    with pytest.raises(SearchTaxonomyError, match="repeats row"):
        load_search_taxonomy(workbook_path=path, expected_sha256=expected)


def test_authority_exposes_no_mutation_api(taxonomy):
    with pytest.raises(Exception):
        taxonomy.workbook_sha256 = "0" * 64
    assert not any(
        name for name in dir(taxonomy) if name.startswith(("add_", "set_", "write_", "save"))
    )


# --------------------------------------------------------------------------------------
# Vocabulary, normalization and the blank-header column
# --------------------------------------------------------------------------------------


def test_canonical_display_values_are_preserved_verbatim(taxonomy):
    assert "居家生活 " in taxonomy.canonical_values("sales_category_lv1")
    assert "Grocery " in taxonomy.canonical_values("sales_category_lv1")
    assert "Grocery" in taxonomy.canonical_values("sales_category_lv2")


def test_lv2_vocabulary_continues_past_the_end_of_lv1(taxonomy):
    """LV2 is read as an independent column, never as a child of the LV1 cell beside it."""
    lv2 = taxonomy.canonical_values("sales_category_lv2")
    assert "女裝" in lv2 and "男裝" in lv2
    assert "女裝" not in taxonomy.canonical_values("sales_category_lv1")


def test_ideographic_comma_is_a_term_character_not_a_separator(taxonomy):
    assert "直播串接（LINE、FB 等）" in taxonomy.canonical_values("content_tags")
    resolution = taxonomy.resolve("直播串接（LINE、FB 等）")
    assert resolution.status is TaxonomyResolutionStatus.RESOLVED
    assert resolution.field == "content_tags"


def test_blank_header_reference_column_is_not_vocabulary(taxonomy):
    assert taxonomy.resolve("https://example.invalid/ref").status is (
        TaxonomyResolutionStatus.NOT_FOUND
    )


def test_normalization_covers_nfkc_casefold_and_whitespace(taxonomy):
    # Full-width solidus and full-width parentheses fold to their ASCII forms.
    assert taxonomy.resolve("食品/飲料", field="sales_category_lv2").canonical_value == "食品／飲料"
    # Case folds.
    assert taxonomy.resolve("fmcg", field="sales_category_lv1").canonical_value == "Grocery "
    # Trailing space on the canonical, and collapsed inner whitespace on the query.
    assert taxonomy.resolve("  居家生活  ", field="sales_category_lv1").canonical_value == "居家生活 "
    assert taxonomy.resolve("LINE  直播", field="content_tags").canonical_value == (
        "直播串接（LINE、FB 等）"
    )


def test_blank_expansion_terms_are_counted_and_never_become_aliases(taxonomy):
    diagnostic = taxonomy.diagnostic()
    assert diagnostic["blank_expansion_term_count"] == 1
    assert taxonomy.resolve("").status is TaxonomyResolutionStatus.NOT_FOUND
    assert taxonomy.resolve("   ").status is TaxonomyResolutionStatus.NOT_FOUND


def test_diagnostic_reports_collisions_without_resolving_them(taxonomy):
    diagnostic = taxonomy.diagnostic()
    assert diagnostic["fields"]["sales_category_lv1"]["canonical_count"] == 5
    assert diagnostic["fields"]["sales_category_lv2"]["canonical_count"] == 5
    assert diagnostic["fields"]["content_tags"]["canonical_count"] == 3
    # "內著" and "家居服" each name two LV2 canonicals.
    assert diagnostic["intra_field_collision_count"] == 2
    # "居家生活" and "grocery" each name values in two fields.
    assert diagnostic["cross_field_ambiguity_count"] == 2
    assert diagnostic["taxonomy_activated_as_production_default"] is False


# --------------------------------------------------------------------------------------
# Resolution contract
# --------------------------------------------------------------------------------------


def test_canonical_value_resolves_to_itself(taxonomy):
    resolution = taxonomy.resolve("美食")
    assert resolution.status is TaxonomyResolutionStatus.RESOLVED
    assert (resolution.field, resolution.canonical_value, resolution.match_type) == (
        "sales_category_lv1",
        "美食",
        "canonical",
    )


def test_expansion_term_resolves_to_its_canonical(taxonomy):
    resolution = taxonomy.resolve("家居生活")
    assert resolution.status is TaxonomyResolutionStatus.RESOLVED
    assert (resolution.field, resolution.canonical_value, resolution.match_type) == (
        "sales_category_lv1",
        "居家生活 ",
        "expansion",
    )


def test_unknown_term_is_not_found_rather_than_guessed(taxonomy):
    assert taxonomy.resolve("完全不存在的詞").status is TaxonomyResolutionStatus.NOT_FOUND


def test_one_alias_naming_two_canonicals_in_one_field_is_ambiguous(taxonomy):
    resolution = taxonomy.resolve("內著")
    assert resolution.status is TaxonomyResolutionStatus.AMBIGUOUS
    assert resolution.candidates == (
        ("sales_category_lv2", "女裝"),
        ("sales_category_lv2", "男裝"),
    )


def test_one_alias_naming_two_levels_is_ambiguous(taxonomy):
    resolution = taxonomy.resolve("grocery")
    assert resolution.status is TaxonomyResolutionStatus.AMBIGUOUS
    assert {field for field, _ in resolution.candidates} == {
        "sales_category_lv1",
        "sales_category_lv2",
    }


def test_explicit_field_resolves_inside_one_level_only(taxonomy):
    assert taxonomy.resolve("grocery", field="sales_category_lv1").canonical_value == "Grocery "
    assert taxonomy.resolve("grocery", field="sales_category_lv2").canonical_value == "Grocery"


def test_explicit_field_does_not_rescue_a_collision_inside_that_field(taxonomy):
    """Naming the level answers a cross-level question, not a two-canonicals-one-level question."""
    assert taxonomy.resolve("內著", field="sales_category_lv2").status is (
        TaxonomyResolutionStatus.AMBIGUOUS
    )


def test_resolve_rejects_a_field_outside_the_taxonomy_contract(taxonomy):
    with pytest.raises(SearchTaxonomyError):
        taxonomy.resolve("美食", field="brand_name")


# --------------------------------------------------------------------------------------
# Query planner integration
# --------------------------------------------------------------------------------------


def _constraints(plan, field):
    return [item for item in plan.constraints if item.field == field]


def test_lv1_expansion_becomes_a_constraint_carrying_the_indexed_value(taxonomy, catalog):
    plan = build_query_plan("家居生活的案例", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv1")
    # The Authority's display value keeps its trailing space; the constraint carries what the
    # formal index actually holds.
    assert constraint.value == "居家生活"
    assert constraint.operator == "canonical_exact"
    assert constraint.source == "search_taxonomy_authority"
    assert constraint.support_status == "supported"
    assert not plan.execution_blocked


def test_lv2_expansion_becomes_a_constraint(taxonomy, catalog):
    plan = build_query_plan("飲料", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv2")
    assert constraint.value == "食品/飲料"
    assert constraint.operator == "canonical_exact"
    assert not plan.execution_blocked


def test_content_tag_expansion_keeps_the_contains_exact_tag_operator(taxonomy, catalog):
    plan = build_query_plan("開團", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "content_tags")
    assert constraint.value == "團購解決方案"
    assert constraint.operator == "contains_exact_tag"
    assert constraint.source == "search_taxonomy_authority"


def test_content_tag_canonical_resolves_through_the_authority(taxonomy, catalog):
    plan = build_query_plan("直播串接（LINE、FB 等）", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "content_tags")
    assert constraint.value == "直播串接（LINE、FB 等）"
    assert constraint.operator == "contains_exact_tag"


def test_explicit_field_query_resolves_through_the_authority(taxonomy, catalog):
    plan = build_query_plan("sales_category_lv1=家居生活", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv1")
    assert constraint.value == "居家生活"
    assert constraint.source == "explicit_field_parser"
    assert not plan.execution_blocked


def test_explicit_field_query_picks_the_named_level_out_of_a_collision(taxonomy, catalog):
    plan = build_query_plan("sales_category_lv2=女裝", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv2")
    assert constraint.value == "女裝"
    assert not plan.execution_blocked


def test_longest_term_wins_so_a_contained_shorter_term_is_not_also_claimed(taxonomy, catalog):
    """"居家生活相關" must not also register the LV1 value "居家生活" inside it."""
    plan = build_query_plan("居家生活相關", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv2")
    assert constraint.value == "居家生活相關"
    assert _constraints(plan, "sales_category_lv1") == []
    assert not plan.execution_blocked


def test_two_different_terms_in_one_query_each_become_a_constraint(taxonomy, catalog):
    plan = build_query_plan("家居生活 開團", catalog, taxonomy=taxonomy)
    assert [item.value for item in _constraints(plan, "sales_category_lv1")] == ["居家生活"]
    assert [item.value for item in _constraints(plan, "content_tags")] == ["團購解決方案"]
    assert not plan.execution_blocked


# --------------------------------------------------------------------------------------
# Fail closed: ambiguity and known-but-not-indexed
# --------------------------------------------------------------------------------------


def test_ambiguous_term_blocks_execution_instead_of_choosing(taxonomy, catalog):
    plan = build_query_plan("居家生活", catalog, taxonomy=taxonomy)
    assert plan.execution_blocked
    assert plan.effective_abstain_reason == "ambiguous_taxonomy_term"
    assert "taxonomy_ambiguous_term:居家生活" in plan.ambiguity_flags
    assert _constraints(plan, "sales_category_lv1") == []
    assert _constraints(plan, "sales_category_lv2") == []


def test_term_known_to_the_authority_but_absent_from_the_index_blocks_execution(
    taxonomy, catalog
):
    plan = build_query_plan("只有權威沒有索引", catalog, taxonomy=taxonomy)
    assert plan.execution_blocked
    assert plan.effective_abstain_reason == "taxonomy_known_but_not_indexed"
    assert "taxonomy_known_but_not_indexed:sales_category_lv1" in plan.ambiguity_flags
    assert plan.constraints == []


def test_unindexed_content_tag_blocks_execution(taxonomy, catalog):
    plan = build_query_plan("沒有索引的標籤", catalog, taxonomy=taxonomy)
    assert plan.execution_blocked
    assert plan.effective_abstain_reason == "taxonomy_known_but_not_indexed"


def test_ambiguous_term_is_not_reopened_as_a_broad_semantic_search(taxonomy, catalog):
    plan = build_query_plan("居家生活", catalog, taxonomy=taxonomy)
    relaxed = allow_semantic_fallback(plan)
    assert relaxed.execution_blocked
    assert relaxed.effective_abstain_reason == "ambiguous_taxonomy_term"
    assert relaxed.query_mode == "structured_lookup"


def test_known_but_not_indexed_term_is_not_reopened_as_a_broad_semantic_search(
    taxonomy, catalog
):
    plan = build_query_plan("只有權威沒有索引", catalog, taxonomy=taxonomy)
    relaxed = allow_semantic_fallback(plan)
    assert relaxed.execution_blocked
    assert relaxed.effective_abstain_reason == "taxonomy_known_but_not_indexed"


def test_explicit_field_query_still_fails_closed_when_the_value_is_not_indexed(
    taxonomy, catalog
):
    plan = build_query_plan("sales_category_lv1=grocery", catalog, taxonomy=taxonomy)
    assert plan.execution_blocked
    assert plan.effective_abstain_reason == "taxonomy_known_but_not_indexed"
    assert _constraints(plan, "sales_category_lv1") == []


# --------------------------------------------------------------------------------------
# Merchant identity precedence
# --------------------------------------------------------------------------------------


def test_taxonomy_does_not_rebind_a_merchant_name_it_also_lists(taxonomy, catalog):
    """"三風製麵" is both a merchant brand and an LV1 canonical; identity wins."""
    plan = build_query_plan("三風製麵", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "entity_name")
    assert constraint.value == "三風製麵"
    assert _constraints(plan, "sales_category_lv1") == []
    assert [entity.entity_type for entity in plan.resolved_entities] == ["merchant"]


def test_taxonomy_does_not_rebind_a_merchant_handle(taxonomy, catalog):
    plan = build_query_plan("@sanfong", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "merchant_handle")
    assert constraint.value == "sanfong"
    assert _constraints(plan, "sales_category_lv1") == []


def test_merchant_identity_resolution_is_identical_with_and_without_a_taxonomy(
    taxonomy, catalog
):
    without = build_query_plan("三風製麵", catalog)
    with_taxonomy = build_query_plan("三風製麵", catalog, taxonomy=taxonomy)
    assert with_taxonomy.resolved_entities == without.resolved_entities
    assert _constraints(with_taxonomy, "entity_name") == _constraints(without, "entity_name")


# --------------------------------------------------------------------------------------
# Typo handling: suggestion only
# --------------------------------------------------------------------------------------


def test_typo_yields_a_suggestion_and_never_a_constraint(taxonomy, catalog):
    plan = build_query_plan("未進索引標簽", catalog, taxonomy=taxonomy)
    assert plan.constraints == []
    assert any(flag.startswith("taxonomy_typo_suggestion:") for flag in plan.ambiguity_flags)
    assert any("未進索引標籤" in warning for warning in plan.parser_warnings)
    assert plan.effective_abstain_reason != "taxonomy_known_but_not_indexed"


def test_a_typo_near_two_canonicals_suggests_both_rather_than_choosing(taxonomy):
    suggestions = taxonomy.suggest_similar("內著x")
    assert {item.canonical_value for item in suggestions} == {"女裝", "男裝"}


def test_suggestions_are_not_offered_for_a_term_the_authority_knows(taxonomy):
    assert taxonomy.suggest_similar("家居生活") == ()


def test_a_semantic_question_is_not_given_typo_noise(taxonomy, catalog):
    plan = build_query_plan("如何提升客單價成效", catalog, taxonomy=taxonomy)
    assert not any(
        flag.startswith("taxonomy_typo_suggestion:") for flag in plan.ambiguity_flags
    )


# --------------------------------------------------------------------------------------
# Backward compatibility
# --------------------------------------------------------------------------------------


def test_without_a_taxonomy_the_catalog_and_curated_aliases_still_decide(catalog):
    plan = build_query_plan("家居生活", catalog)
    (constraint,) = _constraints(plan, "sales_category_lv1")
    assert constraint.value == CATEGORY_ALIASES["家居生活"]
    assert constraint.source == "field_resolver"
    assert not plan.execution_blocked


def test_without_a_taxonomy_an_authority_only_term_is_not_recognised(catalog):
    plan = build_query_plan("只有權威沒有索引", catalog)
    assert plan.constraints == []
    assert plan.effective_abstain_reason != "taxonomy_known_but_not_indexed"


def test_a_supplied_taxonomy_supersedes_the_curated_alias_map(taxonomy, catalog):
    """One question, one alias source: the Authority answers instead of CATEGORY_ALIASES."""
    plan = build_query_plan("家居生活", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "sales_category_lv1")
    assert constraint.source == "search_taxonomy_authority"


def test_catalog_only_values_still_resolve_when_the_authority_is_silent(taxonomy, catalog):
    """The Authority owns aliases; the catalog still owns what the index contains."""
    plan = build_query_plan("索引獨有標籤", catalog, taxonomy=taxonomy)
    (constraint,) = _constraints(plan, "content_tags")
    assert constraint.value == "索引獨有標籤"
    assert constraint.source == "field_resolver"
    assert not plan.execution_blocked


# --------------------------------------------------------------------------------------
# Pipeline caller graph
# --------------------------------------------------------------------------------------


@pytest.fixture
def indexed_db(tmp_path):
    records = [
        _record("莉朵花藝", "lido", "居家生活", "居家生活相關", ["團購解決方案"], 1),
        _record("三風製麵", "sanfong", "美食", "食品/飲料", [], 2),
    ]
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "taxonomy-index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _brands(results):
    return sorted({result.chunk.metadata.brand_name for result in results})


def test_search_index_uses_a_supplied_taxonomy_to_resolve_an_expansion_term(
    indexed_db, taxonomy
):
    results = search_index("家居生活", db_path=indexed_db, taxonomy=taxonomy)
    assert _brands(results) == ["莉朵花藝"]


def test_search_index_returns_nothing_for_an_ambiguous_taxonomy_term(indexed_db, taxonomy):
    assert search_index("居家生活", db_path=indexed_db, taxonomy=taxonomy) == []


def test_search_index_returns_nothing_for_a_term_the_index_does_not_carry(
    indexed_db, taxonomy
):
    assert search_index("只有權威沒有索引", db_path=indexed_db, taxonomy=taxonomy) == []


def test_search_index_without_a_taxonomy_is_unchanged(indexed_db):
    """The same ambiguous word is ordinary free text when no Authority is pinned."""
    plan = build_index_query_plan("居家生活", indexed_db)
    assert plan.effective_abstain_reason != "ambiguous_taxonomy_term"


def test_caller_filters_do_not_reopen_an_ambiguous_taxonomy_term(indexed_db, taxonomy):
    filters = SearchFilters(record_type=["merchant_case"])
    plan = build_index_query_plan("居家生活", indexed_db, filters, taxonomy=taxonomy)
    assert plan.execution_blocked
    assert plan.effective_abstain_reason == "ambiguous_taxonomy_term"
    assert search_index("居家生活", db_path=indexed_db, filters=filters, taxonomy=taxonomy) == []


def test_explain_query_names_the_authority_it_answered_under(indexed_db, taxonomy):
    payload = explain_query("居家生活", db_path=indexed_db, taxonomy=taxonomy)
    assert payload["search_taxonomy"] == {
        "pinned": True,
        "workbook_path": taxonomy.workbook_path,
        "workbook_sha256": taxonomy.workbook_sha256,
    }
    assert payload["execution_blocked"] is True
    assert payload["query_plan"]["abstain_reason"] == "ambiguous_taxonomy_term"


def test_explain_query_records_that_no_authority_was_pinned(indexed_db):
    payload = explain_query("居家生活", db_path=indexed_db)
    assert payload["search_taxonomy"] == {"pinned": False}


def test_ask_index_abstains_on_an_ambiguous_taxonomy_term(indexed_db, taxonomy):
    answer = ask_index("居家生活", db_path=indexed_db, taxonomy=taxonomy)
    assert answer.citations == []


def test_ask_index_answers_a_resolvable_taxonomy_term(indexed_db, taxonomy):
    answer = ask_index("家居生活", db_path=indexed_db, taxonomy=taxonomy)
    assert [citation.title for citation in answer.citations]


def test_agent_ask_receives_the_same_taxonomy_object(indexed_db, taxonomy):
    answer = agent_ask("居家生活", db_path=indexed_db, taxonomy=taxonomy)
    assert answer.generated.citations == []


def _record(brand_name, handle, lv1, lv2, content_tags, source_row):
    title = f"{brand_name}案例"
    metadata = DocumentMetadata(
        title=title,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"商家夥伴案例資料庫:{source_row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=source_row,
        brand_name=brand_name,
        merchant_handle=handle,
        merchant_status="現有商家",
        interview_year=2026,
        sales_category_lv1=lv1,
        sales_category_lv2=lv2,
        content_tags=content_tags,
        article_title=title,
        data_classification="public",
        can_quote_externally=True,
    )
    return metadata, f"{title}\n{brand_name}"


# --------------------------------------------------------------------------------------
# CLI opt-in
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        ["--search-taxonomy-workbook", "taxonomy.xlsx"],
        ["--search-taxonomy-sha256", "0" * 64],
    ],
)
def test_cli_refuses_half_a_taxonomy_pin(tmp_path, capsys, extra):
    assert main(["search", "團購", "--db", str(tmp_path / "index.sqlite"), *extra]) == 2
    assert "must be given together" in capsys.readouterr().err


def test_cli_has_no_production_taxonomy_default(tmp_path):
    from marketing_knowledge_agent.cli import build_parser

    args = build_parser().parse_args(["search", "團購", "--db", str(tmp_path / "i.sqlite")])
    assert args.search_taxonomy_workbook is None
    assert args.search_taxonomy_sha256 is None


def test_cli_reports_a_taxonomy_pin_that_does_not_match(tmp_path, capsys):
    path = tmp_path / "taxonomy.xlsx"
    write_taxonomy_workbook(path)
    exit_code = main(
        [
            "search",
            "團購",
            "--db",
            str(tmp_path / "index.sqlite"),
            "--search-taxonomy-workbook",
            str(path),
            "--search-taxonomy-sha256",
            "0" * 64,
        ]
    )
    assert exit_code == 2
    assert "search taxonomy error" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# Short CJK alias boundary rule (Consolidated Blocker Remediation R1, B1)
# --------------------------------------------------------------------------------------
#
# CJK writes without spaces, so a one- or two-character alias matches inside longer words that
# mean something else. These tests use their own workbook rather than the shared fixture, because
# the point is the *shape* of the vocabulary: single characters, two-character terms, and a longer
# term that must keep matching normally.


SHORT_ALIAS_SALES_ROWS = [
    SALES_CATEGORY_HEADERS,
    # 狗 and 魚 are single characters; 倉鼠 is two. All three appear inside longer ordinary words.
    ["寵物大類", "寵物用品", "寵物", "狗, 魚, 倉鼠, 寵物用品專區"],
    # 停業 is two characters and reverses the meaning of the sentence it hides in.
    ["營運狀態", "營運", "已關閉", "停業"],
    # A long term that must keep resolving from inside a sentence.
    ["美食", "食品", "食品／飲料", "手搖飲料專門店"],
]
SHORT_ALIAS_TAG_ROWS = [
    CONTENT_TAG_HEADERS,
    # ASCII short terms keep their own boundary protection and must not be caught by this rule.
    ["操作易用性", "ux, tv, 3c", None],
]


@pytest.fixture
def short_alias_taxonomy(tmp_path):
    path = tmp_path / "short-alias-taxonomy.xlsx"
    expected = write_taxonomy_workbook(
        path, sales_rows=SHORT_ALIAS_SALES_ROWS, tag_rows=SHORT_ALIAS_TAG_ROWS
    )
    return load_search_taxonomy(workbook_path=path, expected_sha256=expected)


@pytest.fixture
def short_alias_catalog():
    return QueryCatalog(
        merchant_names=["熱狗堡專賣"],
        sales_category_lv1=["寵物大類", "營運狀態", "美食"],
        sales_category_lv2=["寵物", "已關閉", "食品/飲料"],
        content_tags=["操作易用性"],
    )


def _taxonomy_fields(plan):
    return [
        (item.field, item.value) for item in plan.constraints if item.field in TAXONOMY_FIELDS
    ]


@pytest.mark.parametrize(
    "query",
    [
        "熱狗堡品牌的案例",   # 狗 embedded mid-word
        "狗屋設計",           # 狗 embedded at the start
        "魚市場行銷",         # 魚 embedded at the start
        "倉鼠般忙碌的雙11",   # two-character alias inside a simile
        "停業後重新開店的品牌",  # two-character alias, inverted meaning
    ],
)
def test_a_short_cjk_alias_inside_a_longer_word_binds_nothing(
    query, short_alias_taxonomy, short_alias_catalog
):
    plan = build_query_plan(query, short_alias_catalog, short_alias_taxonomy)
    assert _taxonomy_fields(plan) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("狗", ("sales_category_lv2", "寵物")),
        ("倉鼠", ("sales_category_lv2", "寵物")),
        ("停業", ("sales_category_lv2", "已關閉")),
        # A boundary the script itself supplies: punctuation and whitespace both end a CJK run.
        ("狗 的案例", ("sales_category_lv2", "寵物")),
        ("「倉鼠」", ("sales_category_lv2", "寵物")),
    ],
)
def test_a_free_standing_short_cjk_alias_still_binds(
    query, expected, short_alias_taxonomy, short_alias_catalog
):
    plan = build_query_plan(query, short_alias_catalog, short_alias_taxonomy)
    assert _taxonomy_fields(plan) == [expected]


def test_the_rule_does_not_touch_ascii_short_aliases(short_alias_taxonomy, short_alias_catalog):
    """ASCII terms already assert a boundary; this rule must not take that away or duplicate it."""
    for query in ("ux", "tv", "3c"):
        plan = build_query_plan(query, short_alias_catalog, short_alias_taxonomy)
        assert _taxonomy_fields(plan) == [("content_tags", "操作易用性")], query


def test_the_rule_does_not_touch_longer_cjk_terms(short_alias_taxonomy, short_alias_catalog):
    """A term long enough to be specific keeps matching inside a sentence, as it always did."""
    plan = build_query_plan(
        "想看手搖飲料專門店的案例", short_alias_catalog, short_alias_taxonomy
    )
    assert _taxonomy_fields(plan) == [("sales_category_lv2", "食品/飲料")]


def test_an_explicit_field_is_the_user_supplying_the_boundary(
    short_alias_taxonomy, short_alias_catalog
):
    """Naming the field states the domain, so the short-alias rule must not reach it."""
    plan = build_query_plan(
        "sales_category_lv2=寵物", short_alias_catalog, short_alias_taxonomy
    )
    assert _taxonomy_fields(plan) == [("sales_category_lv2", "寵物")]


def test_a_suppressed_short_alias_keeps_the_planners_own_semantics(
    short_alias_taxonomy, short_alias_catalog
):
    """Suppression is not a new refusal: it hands the query back to the non-taxonomy planner.

    Nothing is forced into a taxonomy ambiguity, and no ambiguity flag is raised -- an ambiguity
    flag is read downstream as a reason to disable exact merchant-alias expansion, so inventing one
    here would narrow an unrelated retrieval path.
    """
    with_authority = build_query_plan(
        "熱狗堡品牌的案例", short_alias_catalog, short_alias_taxonomy
    )
    without_authority = build_query_plan("熱狗堡品牌的案例", short_alias_catalog)

    assert with_authority.effective_abstain_reason == without_authority.effective_abstain_reason
    assert with_authority.query_mode == without_authority.query_mode
    assert not any(
        flag.startswith("taxonomy_ambiguous_term") for flag in with_authority.ambiguity_flags
    )


def test_merchant_identity_still_outranks_a_suppressed_short_alias(
    short_alias_taxonomy, short_alias_catalog
):
    plan = build_query_plan("熱狗堡專賣", short_alias_catalog, short_alias_taxonomy)
    assert _taxonomy_fields(plan) == []
    assert [entity.canonical_name for entity in plan.resolved_entities] == ["熱狗堡專賣"]


# --------------------------------------------------------------------------------------
# Synthetic workbook writer
# --------------------------------------------------------------------------------------


def _write_xlsx(path: Path, sheets: dict) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
                for index, name in enumerate(sheets, start=1)
            )
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Relationships>",
        )
        for index, rows in enumerate(sheets.values(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))


def _sheet_xml(rows):
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = f"{_column_letters(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(row_xml) + "</sheetData></worksheet>"
    )


def _column_letters(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters
