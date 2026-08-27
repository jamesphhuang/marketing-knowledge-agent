"""Contract tests for the read-only Slack facet catalog (search_facets.py).

The fixture content index and taxonomy are small and synthetic, but deliberately exercise the
eligibility rules the spec calls out: external governance (draft status), the restricted-customer
denylist, document-level (not chunk-level) dedupe, and the Authority/index intersection for LV2 and
content tags. LV1 never appears anywhere in this module by construction -- ``FacetCatalog`` has no
field for it.

Each governance path is isolated to its own dedicated year/LV2 value so a test failure points at
exactly one rule: "男裝"/2023 is carried only by a draft record, and "女裝"/2022 is carried only by
a record this fixture separately denylists.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.search_facets import FacetCatalogError, build_facet_catalog
from marketing_knowledge_agent.search_taxonomy import load_search_taxonomy

from test_search_taxonomy import write_taxonomy_workbook


SALES_CATEGORY_ROWS = [
    ["Sales Category LV1", "Sales Category LV1 擴充詞", "Sales Category LV2", "Sales Category LV2 擴充詞"],
    ["居家生活", None, "居家生活相關", None],
    [None, None, "食品/飲料", "美食, 餐飲"],
    [None, None, "男裝", None],
    [None, None, "女裝", None],
    [None, None, "未進索引LV2", None],
]
CONTENT_TAG_ROWS = [
    ["內容相關標籤", "內容相關標籤 擴充詞", None],
    ["會員經營", "會員回購", None],
    ["數位轉型", None, None],
    ["未進索引標籤", None, None],
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
    status="published",
    can_quote_externally=True,
    source_row=1,
    content_length=1,
):
    metadata = DocumentMetadata(
        title=brand_name,
        source_type="database",
        record_type="merchant_case",
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
        article_title=brand_name,
        data_classification="public",
        can_quote_externally=can_quote_externally,
    )
    return metadata, "\n".join([brand_name] * content_length)


def _default_records():
    return [
        _metadata("莉朵花藝", "lido", "居家生活相關", ["會員經營"], 2025, source_row=1),
        _metadata("大春煉皂", "dachun", "食品/飲料", ["數位轉型", "會員經營"], 2024, source_row=2),
        _metadata(
            "三風製麵", "shanfeng", "食品/飲料", ["數位轉型"], 2024, source_row=3, content_length=400
        ),
        _metadata("Draft Brand", "draftbrand", "男裝", [], 2023, status="draft", source_row=4),
        _metadata("Restricted Brand", "restrictedbrand", "女裝", ["會員經營"], 2022, source_row=5),
    ]


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


def test_years_are_sorted_newest_first(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    restricted_path = _restricted_customers_path(tmp_path, ["Restricted Brand"])

    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=restricted_path)

    years = [option.year for option in catalog.interview_years]
    assert years == sorted(years, reverse=True)
    assert years == [2025, 2024]


def test_draft_status_is_excluded_by_external_governance(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())

    # No restricted-customer path at all here, so only external governance (draft status) is in
    # play: 2023/"男裝" is carried solely by the draft record and must vanish on its own.
    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    assert catalog.is_valid_year(2023) is False
    assert catalog.is_valid_sales_category_lv2("男裝") is False


def test_restricted_customer_denylist_excludes_eligible_counts(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())

    # "女裝"/2022's only carrier is "published" and externally quotable, so without a denylist it
    # is eligible ...
    without_denylist = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)
    assert without_denylist.is_valid_year(2022) is True
    assert without_denylist.is_valid_sales_category_lv2("女裝") is True

    # ... and with the denylist naming that exact brand, both vanish.
    restricted_path = _restricted_customers_path(tmp_path, ["Restricted Brand"])
    with_denylist = build_facet_catalog(db_path, taxonomy, restricted_customers_path=restricted_path)
    assert with_denylist.is_valid_year(2022) is False
    assert with_denylist.is_valid_sales_category_lv2("女裝") is False


def test_document_id_dedupe_counts_one_case_not_one_per_chunk(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())

    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    # 三風製麵's document is long enough to be split into several chunks by chunk_documents; its
    # eligible count must still be 1, not the chunk count -- so 食品/飲料's total is exactly two
    # documents (大春煉皂 + 三風製麵), not more.
    lv2_option = next(o for o in catalog.sales_category_lv2 if o.canonical_value == "食品/飲料")
    assert lv2_option.eligible_count == 2


def test_lv1_is_never_present_on_the_facet_catalog(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    assert not hasattr(catalog, "sales_category_lv1")
    assert "sales_category_lv1" not in vars(catalog)


def test_lv2_only_offered_when_it_is_both_authority_canonical_and_indexed(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    lv2_values = {option.canonical_value for option in catalog.sales_category_lv2}
    # The Authority states "未進索引LV2" as canonical, but no document ever carries it.
    assert "未進索引LV2" not in lv2_values
    assert "居家生活相關" in lv2_values
    assert "食品/飲料" in lv2_values


def test_content_tag_only_offered_when_it_is_both_authority_canonical_and_indexed(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    tag_values = {option.canonical_value for option in catalog.content_tags}
    assert "未進索引標籤" not in tag_values
    assert "會員經營" in tag_values
    assert "數位轉型" in tag_values


def test_catalog_version_is_reproducible_for_identical_inputs(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())

    first = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)
    second = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    assert first.catalog_version == second.catalog_version
    assert first.content_index_generation_id == second.content_index_generation_id


def test_catalog_version_changes_when_the_content_index_changes(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    before = build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    extra_records = _default_records() + [
        _metadata("New Brand", "newbrand", "男裝", [], 2026, source_row=6)
    ]
    db_path2 = _build_index(tmp_path, extra_records, name="content_index_v2.sqlite")
    after = build_facet_catalog(db_path2, taxonomy, restricted_customers_path=None)

    assert before.catalog_version != after.catalog_version


def test_catalog_version_changes_when_the_taxonomy_authority_changes(tmp_path):
    db_path = _build_index(tmp_path, _default_records())
    path_a = tmp_path / "taxonomy_a.xlsx"
    sha_a = write_taxonomy_workbook(path_a, sales_rows=SALES_CATEGORY_ROWS, tag_rows=CONTENT_TAG_ROWS)
    taxonomy_a = load_search_taxonomy(workbook_path=path_a, expected_sha256=sha_a)

    tag_rows_b = CONTENT_TAG_ROWS + [["額外標籤", None, None]]
    path_b = tmp_path / "taxonomy_b.xlsx"
    sha_b = write_taxonomy_workbook(path_b, sales_rows=SALES_CATEGORY_ROWS, tag_rows=tag_rows_b)
    taxonomy_b = load_search_taxonomy(workbook_path=path_b, expected_sha256=sha_b)

    catalog_a = build_facet_catalog(db_path, taxonomy_a, restricted_customers_path=None)
    catalog_b = build_facet_catalog(db_path, taxonomy_b, restricted_customers_path=None)

    assert catalog_a.catalog_version != catalog_b.catalog_version


def test_taxonomy_workbook_and_content_index_are_untouched_by_building_the_catalog(tmp_path, taxonomy):
    db_path = _build_index(tmp_path, _default_records())
    before_db = db_path.read_bytes()
    workbook_path = Path(taxonomy.workbook_path)
    before_workbook = workbook_path.read_bytes()

    build_facet_catalog(db_path, taxonomy, restricted_customers_path=None)

    assert db_path.read_bytes() == before_db
    assert workbook_path.read_bytes() == before_workbook


def test_missing_content_index_fails_closed(tmp_path, taxonomy):
    with pytest.raises(FacetCatalogError):
        build_facet_catalog(tmp_path / "absent.sqlite", taxonomy, restricted_customers_path=None)
