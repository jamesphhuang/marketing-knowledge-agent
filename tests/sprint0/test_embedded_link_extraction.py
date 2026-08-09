from __future__ import annotations

from copy import deepcopy

import pytest

from marketing_knowledge_agent.cell_normalization import (
    FieldContract,
    FieldValueKind,
    normalize_source_cell,
)
from marketing_knowledge_agent.google_normalization import (
    ExcludedSourceRef,
    ExclusionReason,
    MetricSourceCells,
)
from marketing_knowledge_agent.link_resolution import (
    AssetSourceSlot,
    EligibleAssetLinkCell,
    LinkExtractionError,
    LinkSource,
    extract_link_candidates,
)
from marketing_knowledge_agent.sheets_contracts import (
    CellData,
    GoogleValue,
    SheetSnapshot,
    SpreadsheetSnapshot,
    TextFormatLink,
    TextFormatRun,
)


def _eligible_cell(
    *,
    formatted: str | None = "Synthetic Asset",
    hyperlink: str | None = None,
    formula: str | None = None,
    runs: tuple[TextFormatRun, ...] = (),
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
) -> EligibleAssetLinkCell:
    column = {
        AssetSourceSlot.ARTICLE: 7,
        AssetSourceSlot.VIDEO: 8,
        AssetSourceSlot.PODCAST: 9,
        AssetSourceSlot.NEWS: 10,
    }[slot]
    source = CellData(
        row_index=6,
        column_index=column,
        formatted_value=formatted,
        effective_value=(
            GoogleValue(string_value=formatted)
            if formatted is not None
            else None
        ),
        user_entered_value=(
            GoogleValue(formula_value=formula) if formula is not None else None
        ),
        hyperlink=hyperlink,
        text_format_runs=runs,
    )
    snapshot = SpreadsheetSnapshot(
        spreadsheet_id="synthetic-spreadsheet-wp6",
        sheets=(
            SheetSnapshot(
                sheet_id=106,
                title="Synthetic Asset Sources",
                row_count=12,
                column_count=12,
                cells=(source,),
            ),
        ),
    )
    resolved = normalize_source_cell(
        snapshot,
        sheet_id=106,
        source_row_index=6,
        field_contract=FieldContract(
            field_name=f"synthetic_{slot.value}_source",
            value_kind=FieldValueKind.TEXT,
            source_column_index=column,
        ),
        source_fingerprint="sha256:synthetic-wp6-source",
        sync_batch_id="SYNTHETIC-WP6-BATCH",
    )
    return EligibleAssetLinkCell(resolved_cell=resolved, asset_source_slot=slot)


def _run(start_index: int, raw_url: str) -> TextFormatRun:
    return TextFormatRun(
        start_index=start_index,
        link=TextFormatLink(uri=raw_url),
    )


def test_rich_text_single_link_produces_one_candidate_with_provenance():
    candidates = extract_link_candidates(
        _eligible_cell(runs=(_run(0, "https://example.test/article"),))
    )

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.RICH_TEXT, "https://example.test/article")
    ]
    candidate = candidates[0]
    assert candidate.asset_source_slot is AssetSourceSlot.ARTICLE
    assert candidate.lineage.sheet_id == 106
    assert candidate.lineage.sheet_title == "Synthetic Asset Sources"
    assert candidate.lineage.source_coordinate == (6, 7)
    assert candidate.field_lineage.target_coordinate == (6, 7)
    assert candidate.run_start_index == 0
    assert candidate.run_ordinal == 0


def test_rich_text_multiple_runs_and_duplicate_occurrences_are_all_retained():
    candidates = extract_link_candidates(
        _eligible_cell(
            runs=(
                _run(0, "https://example.test/same"),
                _run(8, "https://example.test/same"),
                _run(20, "https://example.test/different"),
            )
        )
    )

    assert [candidate.raw_url for candidate in candidates] == [
        "https://example.test/same",
        "https://example.test/same",
        "https://example.test/different",
    ]
    assert [candidate.run_start_index for candidate in candidates] == [0, 8, 20]
    assert [candidate.run_ordinal for candidate in candidates] == [0, 1, 2]


def test_lower_priority_sources_do_not_short_circuit_and_duplicates_remain():
    raw_url = "https://example.test/same"
    candidates = extract_link_candidates(
        _eligible_cell(
            formatted=raw_url,
            hyperlink=raw_url,
            formula=f'=HYPERLINK("{raw_url}", "Synthetic Asset")',
            runs=(_run(0, raw_url),),
        )
    )

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.RICH_TEXT, raw_url),
        (LinkSource.CELL_HYPERLINK, raw_url),
        (LinkSource.HYPERLINK_FORMULA, raw_url),
        (LinkSource.LITERAL_TEXT, raw_url),
    ]


def test_all_distinct_sources_are_retained_in_frozen_priority_order():
    candidates = extract_link_candidates(
        _eligible_cell(
            formatted="https://example.test/literal",
            hyperlink="https://example.test/cell",
            formula='=HYPERLINK("https://example.test/formula")',
            runs=(
                _run(0, "https://example.test/rich-a"),
                _run(10, "https://example.test/rich-b"),
            ),
        )
    )

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.RICH_TEXT, "https://example.test/rich-a"),
        (LinkSource.RICH_TEXT, "https://example.test/rich-b"),
        (LinkSource.CELL_HYPERLINK, "https://example.test/cell"),
        (LinkSource.HYPERLINK_FORMULA, "https://example.test/formula"),
        (LinkSource.LITERAL_TEXT, "https://example.test/literal"),
    ]


def test_whole_cell_hyperlink_is_collected_without_wp7_filtering():
    raw_url = "mailto:synthetic@example.test"
    candidates = extract_link_candidates(_eligible_cell(hyperlink=raw_url))

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.CELL_HYPERLINK, raw_url)
    ]


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        (
            '=HYPERLINK("https://example.test/article")',
            "https://example.test/article",
        ),
        (
            '=hyperlink("https://example.test/article", "Synthetic Asset")',
            "https://example.test/article",
        ),
        (
            '=HYPERLINK("https://example.test/a""b", "Synthetic Asset")',
            'https://example.test/a"b',
        ),
    ],
    ids=("quoted-url", "optional-display-argument", "escaped-double-quote"),
)
def test_hyperlink_formula_extracts_only_static_first_argument(formula, expected):
    candidates = extract_link_candidates(_eligible_cell(formula=formula))

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.HYPERLINK_FORMULA, expected)
    ]


def test_non_hyperlink_formula_produces_no_formula_candidate():
    assert extract_link_candidates(
        _eligible_cell(formula='=CONCAT("https://example.test/", "article")')
    ) == ()


@pytest.mark.parametrize(
    "formula",
    [
        "=HYPERLINK()",
        '=HYPERLINK("https://example.test/article"',
        '=HYPERLINK(1 + 1, "Synthetic Asset")',
        '=HYPERLINK(A1, "Synthetic Asset")',
        '=HYPERLINK("", "Synthetic Asset")',
        '=HYPERLINK("https://example.test/article",)',
    ],
    ids=(
        "missing-first-argument",
        "missing-closing-parenthesis",
        "expression-first-argument",
        "cell-reference-first-argument",
        "empty-first-argument",
        "missing-display-argument",
    ),
)
def test_malformed_or_dynamic_hyperlink_formula_fails_closed_without_guessing(formula):
    with pytest.raises(LinkExtractionError) as caught:
        extract_link_candidates(_eligible_cell(formula=formula))

    assert caught.value.code in {
        "HYPERLINK_FORMULA_MALFORMED",
        "HYPERLINK_FIRST_ARGUMENT_NOT_STATIC",
        "HYPERLINK_FIRST_ARGUMENT_EMPTY",
    }
    assert str(caught.value) == caught.value.code
    assert formula not in repr(caught.value)


@pytest.mark.parametrize(
    "literal",
    [
        "http://example.test/article",
        "https://example.test/article",
    ],
)
def test_literal_fallback_accepts_only_complete_single_http_or_https_text(literal):
    candidates = extract_link_candidates(_eligible_cell(formatted=literal))

    assert [(candidate.source, candidate.raw_url) for candidate in candidates] == [
        (LinkSource.LITERAL_TEXT, literal)
    ]


@pytest.mark.parametrize(
    "display",
    [
        "See https://example.test/article",
        "Example Article",
        " https://example.test/article ",
        "mailto:synthetic@example.test",
        "https://",
    ],
    ids=("prose", "title-only", "surrounding-space", "non-http", "missing-host-shape"),
)
def test_literal_fallback_does_not_search_or_guess(display):
    assert extract_link_candidates(_eligible_cell(formatted=display)) == ()


def test_candidate_sequence_is_deterministic_and_extractor_does_not_mutate_input():
    eligible = _eligible_cell(
        formatted="https://example.test/literal",
        hyperlink="https://example.test/cell",
        formula='=HYPERLINK("https://example.test/formula")',
        runs=(
            _run(0, "https://example.test/rich-a"),
            _run(12, "https://example.test/rich-b"),
        ),
        slot=AssetSourceSlot.NEWS,
    )
    before = deepcopy(eligible)

    first = extract_link_candidates(eligible)
    second = extract_link_candidates(eligible)

    assert first == second
    assert eligible == before
    assert all(candidate.asset_source_slot is AssetSourceSlot.NEWS for candidate in first)


@pytest.mark.parametrize(
    "unclassified",
    [
        CellData(row_index=6, column_index=7, formatted_value="https://example.test/raw"),
        MetricSourceCells(
            metric_id=None,
            metric_type="Synthetic",
            indicator="Synthetic",
            approved_statement="Synthetic statement",
            note=None,
            maintenance_updated_at=None,
            evidence_urls=("https://example.test/evidence",),
            channel_cells=(),
            lifecycle_status=None,
            review_status=None,
            publish_eligibility=None,
            can_quote_externally=False,
            source_lineage=None,
        ),
        ExcludedSourceRef(
            sheet_id=106,
            source_row=7,
            reason=ExclusionReason.ORAL_ONLY,
            source_digest="sha256:" + "0" * 64,
        ),
    ],
    ids=("raw-cell-data", "raw-metric-source", "excluded-oral-only-reference"),
)
def test_unclassified_metric_and_oral_only_inputs_cannot_bypass_boundary(unclassified):
    with pytest.raises(
        LinkExtractionError,
        match="ELIGIBLE_ASSET_LINK_CELL_REQUIRED",
    ):
        extract_link_candidates(unclassified)


def test_eligible_boundary_rejects_wrong_content_asset_column():
    eligible = _eligible_cell(slot=AssetSourceSlot.ARTICLE)

    with pytest.raises(
        LinkExtractionError,
        match="ASSET_SOURCE_SLOT_COLUMN_MISMATCH",
    ):
        EligibleAssetLinkCell(
            resolved_cell=eligible.resolved_cell,
            asset_source_slot=AssetSourceSlot.VIDEO,
        )


def test_malformed_rich_link_reports_safe_issue():
    with pytest.raises(
        LinkExtractionError,
        match="RICH_TEXT_LINK_URI_INVALID",
    ) as caught:
        extract_link_candidates(_eligible_cell(runs=(_run(0, "   "),)))

    assert caught.value.source is LinkSource.RICH_TEXT
    assert caught.value.run_ordinal == 0
    assert "   " not in repr(caught.value)


def test_sensitive_raw_candidate_is_transient_and_redacted_from_repr_and_logs(caplog):
    raw_url = "https://example.test/path?token=SYNTHETIC_SECRET"
    candidate = extract_link_candidates(_eligible_cell(hyperlink=raw_url))[0]

    assert candidate.raw_url == raw_url
    assert raw_url not in repr(candidate)
    assert "SYNTHETIC_SECRET" not in caplog.text
    assert candidate.raw_url.endswith("token=SYNTHETIC_SECRET")
