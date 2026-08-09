from __future__ import annotations

import builtins
from dataclasses import asdict, fields, replace

import pytest

import marketing_knowledge_agent.canonical_models as canonical_models
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    BrandId,
    ContentAssetKey,
    SourceRecordId,
)
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.link_resolution import (
    AssetResolution,
    AssetResolutionError,
    AssetResolutionStatus,
    AssetSourceSlot,
    ContentAssetCandidate,
    LinkCandidate,
    LinkSource,
    resolve_content_asset,
)
from marketing_knowledge_agent.url_safety import (
    URLRejectionCode,
    URLValidationResult,
    validate_and_canonicalize_url,
)


SYNTHETIC_UNSAFE_URL = "https://example.test/path?token=SYNTHETIC_WP8_SECRET"

_SLOT_TO_TYPE = {
    AssetSourceSlot.ARTICLE: AssetType.ARTICLE,
    AssetSourceSlot.VIDEO: AssetType.VIDEO,
    AssetSourceSlot.PODCAST: AssetType.PODCAST,
    AssetSourceSlot.NEWS: AssetType.NEWS,
}
_SLOT_TO_COLUMN = {
    AssetSourceSlot.ARTICLE: 7,
    AssetSourceSlot.VIDEO: 8,
    AssetSourceSlot.PODCAST: 9,
    AssetSourceSlot.NEWS: 10,
}


def _lineage(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
) -> tuple[SourceLineage, SourceFieldLineage]:
    column = _SLOT_TO_COLUMN[slot]
    lineage = SourceLineage(
        spreadsheet_id="synthetic-spreadsheet-wp8",
        sheet_id=sheet_id,
        sheet_title="Synthetic Content Assets",
        sheet_hidden=False,
        source_row_index=row,
        source_column_index=column,
        source_fingerprint="sha256:synthetic-wp8-source",
        sync_batch_id="SYNTHETIC-WP8-BATCH",
    )
    field_lineage = SourceFieldLineage(
        field_name=f"{slot.value}_asset",
        target_row_index=row,
        target_column_index=column,
        value_row_index=row,
        value_column_index=column,
        merge_anchor_row_index=None,
        merge_anchor_column_index=None,
        merge_range=None,
        inherited_from_merge=False,
        inheritance_reason=InheritanceReason.LOCAL,
    )
    return lineage, field_lineage


def _candidate(
    raw_url: str,
    *,
    source: LinkSource = LinkSource.CELL_HYPERLINK,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    run_start_index: int | None = None,
    run_ordinal: int | None = None,
) -> LinkCandidate:
    lineage, field_lineage = _lineage(slot=slot, row=row, sheet_id=sheet_id)
    return LinkCandidate(
        raw_url=raw_url,
        source=source,
        asset_source_slot=slot,
        lineage=lineage,
        field_lineage=field_lineage,
        run_start_index=run_start_index,
        run_ordinal=run_ordinal,
    )


def _resolve(
    *,
    title: str | None = "Example Asset",
    candidates: tuple[LinkCandidate, ...] = (),
    results: tuple[URLValidationResult, ...] | None = None,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    brand_id: BrandId = BrandId("BRD-0001"),
    asset_key: ContentAssetKey | None = None,
):
    lineage, field_lineage = _lineage(slot=slot, row=row)
    if results is None:
        results = tuple(validate_and_canonicalize_url(item) for item in candidates)
    if asset_key is None:
        asset_key = ContentAssetKey(
            source_record_id=SourceRecordId("MREC-0001"),
            asset_type=_SLOT_TO_TYPE[slot],
        )
    return resolve_content_asset(
        asset_key=asset_key,
        brand_id=brand_id,
        normalized_title=title,
        lineage=lineage,
        field_lineage=field_lineage,
        candidates=candidates,
        validation_results=results,
    )


def test_absent_title_and_no_candidate_returns_no_asset():
    assert _resolve(title=None) is None


@pytest.mark.parametrize("title", ["", " ", "\t\n"])
def test_blank_title_is_rejected_instead_of_renormalized(title):
    with pytest.raises(AssetResolutionError, match="ASSET_TITLE_NOT_NORMALIZED"):
        _resolve(title=title)


@pytest.mark.parametrize("slot", tuple(AssetSourceSlot))
def test_title_and_zero_safe_url_is_incomplete_for_each_asset_type(slot):
    resolution = _resolve(title="Example Asset", slot=slot)

    assert resolution.status is AssetResolutionStatus.INCOMPLETE
    assert resolution.asset_key == ContentAssetKey(
        SourceRecordId("MREC-0001"),
        _SLOT_TO_TYPE[slot],
    )
    assert resolution.candidates == ()
    assert resolution.rejected_occurrences == ()


def test_one_safe_url_is_resolved_candidate_with_one_logical_key():
    candidate = _candidate("https://example.test/story")

    resolution = _resolve(candidates=(candidate,))

    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert str(resolution.asset_key) == "MREC-0001:article"
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].asset_key is resolution.asset_key
    assert resolution.candidates[0].canonical_url.value == (
        "https://example.test/story"
    )


def test_url_only_asset_without_title_is_resolved_candidate():
    candidate = _candidate(
        "https://example.test/video",
        slot=AssetSourceSlot.VIDEO,
    )

    resolution = _resolve(
        title=None,
        candidates=(candidate,),
        slot=AssetSourceSlot.VIDEO,
    )

    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert resolution.title is None
    assert str(resolution.asset_key) == "MREC-0001:video"


def test_canonical_equal_sources_dedupe_and_retain_every_provenance_occurrence():
    candidates = (
        _candidate(
            "https://example.test/story?utm_source=rich",
            source=LinkSource.RICH_TEXT,
            run_start_index=0,
            run_ordinal=0,
        ),
        _candidate(
            "https://EXAMPLE.test:443/story#section",
            source=LinkSource.CELL_HYPERLINK,
        ),
        _candidate(
            "https://example.test/story?source=formula",
            source=LinkSource.HYPERLINK_FORMULA,
        ),
        _candidate(
            "https://example.test/story?ref=literal",
            source=LinkSource.LITERAL_TEXT,
        ),
    )

    resolution = _resolve(candidates=candidates)

    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert len(resolution.candidates) == 1
    group = resolution.candidates[0]
    assert group.canonical_url.value == "https://example.test/story"
    assert [item.source for item in group.provenance_occurrences] == [
        LinkSource.RICH_TEXT,
        LinkSource.CELL_HYPERLINK,
        LinkSource.HYPERLINK_FORMULA,
        LinkSource.LITERAL_TEXT,
    ]
    assert group.provenance_occurrences[0].run_start_index == 0
    assert group.provenance_occurrences[0].run_ordinal == 0


def test_two_distinct_safe_urls_need_review_without_winner_or_asset_split():
    candidates = (
        _candidate(
            "https://example.test/higher-priority",
            source=LinkSource.RICH_TEXT,
            run_start_index=0,
            run_ordinal=0,
        ),
        _candidate(
            "https://example.test/lower-priority",
            source=LinkSource.LITERAL_TEXT,
        ),
    )

    resolution = _resolve(candidates=candidates)

    assert resolution.status is AssetResolutionStatus.NEEDS_REVIEW
    assert len(resolution.candidates) == 2
    assert {item.asset_key for item in resolution.candidates} == {
        ContentAssetKey(SourceRecordId("MREC-0001"), AssetType.ARTICLE)
    }
    assert [item.canonical_url.value for item in resolution.candidates] == [
        "https://example.test/higher-priority",
        "https://example.test/lower-priority",
    ]
    assert not hasattr(resolution, "selected_url")
    assert not hasattr(resolution, "winner_url")
    assert not hasattr(resolution, "preferred_url")
    assert not hasattr(resolution, "primary_url")


def test_direct_resolution_rejects_duplicate_canonical_groups():
    resolved = _resolve(candidates=(_candidate("https://example.test/story"),))
    duplicate_group = resolved.candidates[0]

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_RESOLUTION_CANONICAL_GROUP_DUPLICATE",
    ):
        AssetResolution(
            asset_key=resolved.asset_key,
            brand_id=resolved.brand_id,
            title=resolved.title,
            status=AssetResolutionStatus.NEEDS_REVIEW,
            candidates=(duplicate_group, duplicate_group),
            rejected_occurrences=(),
            lineage=resolved.lineage,
            field_lineage=resolved.field_lineage,
        )


def test_canonical_group_order_follows_first_candidate_occurrence_not_lexical_sort():
    candidates = (
        _candidate("https://example.test/z-first"),
        _candidate("https://example.test/a-second", source=LinkSource.LITERAL_TEXT),
    )

    resolution = _resolve(candidates=candidates)

    assert [item.canonical_url.value for item in resolution.candidates] == [
        "https://example.test/z-first",
        "https://example.test/a-second",
    ]


def test_all_rejected_without_title_needs_review_and_retains_only_safe_evidence(
    caplog,
):
    candidates = (
        _candidate(SYNTHETIC_UNSAFE_URL),
        _candidate(
            "https://10.1.2.3/private",
            source=LinkSource.LITERAL_TEXT,
        ),
    )

    resolution = _resolve(title=None, candidates=candidates)

    assert resolution.status is AssetResolutionStatus.NEEDS_REVIEW
    assert str(resolution.asset_key) == "MREC-0001:article"
    assert resolution.candidates == ()
    assert [item.rejection_code for item in resolution.rejected_occurrences] == [
        URLRejectionCode.SENSITIVE_QUERY,
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    ]
    rendered = (repr(resolution), repr(asdict(resolution)), caplog.text)
    assert all(SYNTHETIC_UNSAFE_URL not in value for value in rendered)
    assert all("SYNTHETIC_WP8_SECRET" not in value for value in rendered)


def test_all_rejected_with_title_is_incomplete_not_needs_review():
    candidates = (
        _candidate(SYNTHETIC_UNSAFE_URL),
        _candidate(
            "https://10.1.2.3/private",
            source=LinkSource.LITERAL_TEXT,
        ),
    )

    resolution = _resolve(title="Example Video", candidates=candidates)

    assert resolution.status is AssetResolutionStatus.INCOMPLETE
    assert resolution.candidates == ()
    assert len(resolution.rejected_occurrences) == 2


def test_one_safe_plus_one_rejected_stays_resolved_candidate_without_title():
    candidates = (
        _candidate("https://example.test/safe"),
        _candidate(SYNTHETIC_UNSAFE_URL, source=LinkSource.LITERAL_TEXT),
    )

    resolution = _resolve(title=None, candidates=candidates)

    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].canonical_url.value == (
        "https://example.test/safe"
    )
    assert [item.rejection_code for item in resolution.rejected_occurrences] == [
        URLRejectionCode.SENSITIVE_QUERY
    ]


def test_candidate_result_count_mismatch_fails_before_truth_table_resolution():
    candidates = (
        _candidate(SYNTHETIC_UNSAFE_URL),
        _candidate("https://10.1.2.3/private", source=LinkSource.LITERAL_TEXT),
    )
    one_result = validate_and_canonicalize_url(candidates[0])

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_CANDIDATE_RESULT_COUNT_MISMATCH",
    ):
        _resolve(title=None, candidates=candidates, results=(one_result,))


def test_duplicate_candidate_provenance_fails_closed_as_ambiguous_pairing():
    candidates = (
        _candidate("https://example.test/first"),
        _candidate("https://example.test/second"),
    )
    results = tuple(validate_and_canonicalize_url(item) for item in candidates)

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_CANDIDATE_PROVENANCE_DUPLICATE",
    ):
        _resolve(candidates=candidates, results=results)


def test_candidate_result_provenance_mismatch_fails_closed():
    candidate = _candidate("https://example.test/story")
    result = validate_and_canonicalize_url(candidate)
    unrelated = replace(result, source=LinkSource.LITERAL_TEXT)

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_CANDIDATE_RESULT_PROVENANCE_MISMATCH",
    ):
        _resolve(candidates=(candidate,), results=(unrelated,))


def test_cross_row_candidate_fails_closed():
    candidate = _candidate("https://example.test/story", row=7)

    with pytest.raises(AssetResolutionError, match="ASSET_SOURCE_LINEAGE_MISMATCH"):
        _resolve(candidates=(candidate,))


def test_cross_row_result_fails_pairing_integrity():
    candidate = _candidate("https://example.test/story")
    result = validate_and_canonicalize_url(candidate)
    other_lineage, other_field_lineage = _lineage(row=7)
    unrelated = replace(
        result,
        lineage=other_lineage,
        field_lineage=other_field_lineage,
    )

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_CANDIDATE_RESULT_PROVENANCE_MISMATCH",
    ):
        _resolve(candidates=(candidate,), results=(unrelated,))


def test_wrong_candidate_slot_fails_closed():
    candidate = _candidate(
        "https://example.test/video",
        slot=AssetSourceSlot.VIDEO,
    )

    with pytest.raises(AssetResolutionError, match="ASSET_SOURCE_SLOT_MISMATCH"):
        _resolve(candidates=(candidate,))


def test_wrong_result_slot_fails_pairing_integrity():
    candidate = _candidate("https://example.test/story")
    result = validate_and_canonicalize_url(candidate)
    unrelated = replace(result, asset_source_slot=AssetSourceSlot.VIDEO)

    with pytest.raises(
        AssetResolutionError,
        match="ASSET_CANDIDATE_RESULT_PROVENANCE_MISMATCH",
    ):
        _resolve(candidates=(candidate,), results=(unrelated,))


@pytest.mark.parametrize(
    ("candidates", "results", "code"),
    [
        (({"raw_url": "https://example.test"},), (), "LINK_CANDIDATE_REQUIRED"),
        (
            (),
            ({"canonical_url": "https://example.test"},),
            "URL_VALIDATION_RESULT_REQUIRED",
        ),
    ],
)
def test_generic_dict_cannot_substitute_for_typed_candidate_or_result(
    candidates,
    results,
    code,
):
    with pytest.raises(AssetResolutionError, match=code):
        _resolve(candidates=candidates, results=results)


def test_plain_string_cannot_substitute_for_wp7_canonical_url():
    candidate = _candidate("https://example.test/story")
    trusted = validate_and_canonicalize_url(candidate)
    fabricated = URLValidationResult(
        canonical_url="https://example.test/story",
        rejection_code=None,
        source=trusted.source,
        asset_source_slot=trusted.asset_source_slot,
        lineage=trusted.lineage,
        field_lineage=trusted.field_lineage,
        run_start_index=trusted.run_start_index,
        run_ordinal=trusted.run_ordinal,
    )

    with pytest.raises(AssetResolutionError, match="CANONICAL_URL_REQUIRED"):
        _resolve(candidates=(candidate,), results=(fabricated,))


@pytest.mark.parametrize("asset_key", [None, "MREC-0001:article", "sheet:r7:article"])
def test_missing_raw_or_row_based_key_cannot_substitute_for_content_asset_key(
    asset_key,
):
    lineage, field_lineage = _lineage()
    with pytest.raises(AssetResolutionError, match="CONTENT_ASSET_KEY_REQUIRED"):
        resolve_content_asset(
            asset_key=asset_key,
            brand_id=BrandId("BRD-0001"),
            normalized_title="Example Asset",
            lineage=lineage,
            field_lineage=field_lineage,
            candidates=(),
            validation_results=(),
        )


@pytest.mark.parametrize("brand_id", [None, "BRD-0001"])
def test_unresolved_or_untyped_brand_fails_closed(brand_id):
    with pytest.raises(AssetResolutionError, match="BRAND_ID_REQUIRED"):
        _resolve(brand_id=brand_id)


def test_resolution_source_lineage_must_match_frozen_asset_slot_column():
    asset_key = ContentAssetKey(SourceRecordId("MREC-0001"), AssetType.VIDEO)
    article_lineage, article_field_lineage = _lineage(slot=AssetSourceSlot.ARTICLE)

    with pytest.raises(AssetResolutionError, match="ASSET_SOURCE_SLOT_MISMATCH"):
        resolve_content_asset(
            asset_key=asset_key,
            brand_id=BrandId("BRD-0001"),
            normalized_title="Example Video",
            lineage=article_lineage,
            field_lineage=article_field_lineage,
            candidates=(),
            validation_results=(),
        )


def test_identity_is_stable_across_row_title_url_order_run_and_source_changes():
    before_key = ContentAssetKey(SourceRecordId("MREC-0001"), AssetType.ARTICLE)
    after_key = ContentAssetKey(SourceRecordId("MREC-0001"), AssetType.ARTICLE)
    first_candidates = (
        _candidate(
            "https://example.test/first",
            source=LinkSource.RICH_TEXT,
            run_start_index=0,
            run_ordinal=0,
        ),
        _candidate(
            "https://example.test/second",
            source=LinkSource.LITERAL_TEXT,
        ),
    )
    second_candidates = (
        _candidate(
            "https://example.test/changed-second",
            source=LinkSource.CELL_HYPERLINK,
            row=99,
        ),
        _candidate(
            "https://example.test/changed-first",
            source=LinkSource.RICH_TEXT,
            row=99,
            run_start_index=48,
            run_ordinal=7,
        ),
    )

    before = _resolve(
        title="Example Asset Before",
        candidates=first_candidates,
        asset_key=before_key,
    )
    after = _resolve(
        title="Example Asset After",
        candidates=second_candidates,
        row=99,
        asset_key=after_key,
    )

    assert before_key == after_key
    assert str(before_key) == str(after_key) == "MREC-0001:article"
    assert before.asset_key == after.asset_key
    assert str(before.asset_key) == str(after.asset_key) == "MREC-0001:article"


def test_status_and_dto_surfaces_contain_no_wp9_publish_or_second_identity_fields():
    status_values = {item.value for item in AssetResolutionStatus}
    resolution_fields = {item.name for item in fields(AssetResolution)}
    candidate_fields = {item.name for item in fields(ContentAssetCandidate)}
    forbidden = {
        "active",
        "publishable",
        "searchable",
        "official",
        "archived",
        "captured",
        "capture_status",
        "captured_content_id",
        "winner",
        "selected_url",
        "ast_id",
    }

    assert status_values == {
        "incomplete",
        "resolved_candidate",
        "needs_review",
    }
    assert forbidden.isdisjoint(resolution_fields)
    assert forbidden.isdisjoint(candidate_fields)
    assert not hasattr(canonical_models, "AssetId")
    assert not hasattr(canonical_models, "CapturedContentId")


def test_resolver_does_not_revalidate_fetch_persist_or_use_network(monkeypatch):
    candidate = _candidate("https://example.test/story")
    result = validate_and_canonicalize_url(candidate)

    def unexpected(*args, **kwargs):
        raise AssertionError("WP8 boundary side effect")

    monkeypatch.setattr(
        "marketing_knowledge_agent.url_safety.validate_and_canonicalize_url",
        unexpected,
    )
    monkeypatch.setattr("socket.getaddrinfo", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)
    monkeypatch.setattr(builtins, "open", unexpected)

    resolution = _resolve(candidates=(candidate,), results=(result,))

    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
