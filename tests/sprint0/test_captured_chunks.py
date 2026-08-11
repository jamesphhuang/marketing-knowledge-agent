from __future__ import annotations

import inspect
import re
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

import marketing_knowledge_agent.captured_chunks as captured_chunks
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    BrandId,
    BrandIdentityDecision,
    CanonicalSourceLineage,
    ContentAssetKey,
    LifecycleStatus,
    MetricId,
    PublishEligibility,
    ReviewStatus,
    SourceRecord,
    SourceRecordId,
)
from marketing_knowledge_agent.captured_chunks import (
    CapturedChunk,
    CapturedChunkError,
    CapturedChunkId,
    CapturedChunkMetadata,
    SectionAnchor,
    SyntheticChunkSpan,
    build_captured_chunk,
)
from marketing_knowledge_agent.capture_policy import (
    CaptureMode,
    CapturePolicyDecision,
    FetchFailureCategory,
    PolicyDecisionReason,
)
from marketing_knowledge_agent.captured_content import (
    AuthorityRole,
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
    EvidenceRelationshipId,
    SafeHttpMetadata,
    Section,
)
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.content_hashing import (
    ApprovedLkgFreshnessPolicy,
    CaptureContentHash,
    CaptureRevisionRef,
    LkgEligibilityInput,
    LkgEligibilityResult,
    StaleLkgCandidate,
    evaluate_lkg_reuse,
)
from marketing_knowledge_agent.link_resolution import (
    AssetSourceSlot,
    LinkCandidate,
    LinkSource,
)
from marketing_knowledge_agent.url_safety import validate_and_canonicalize_url


UTC = timezone.utc
CAPTURED_AT = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
LAST_SUCCESS = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)
LAST_ATTEMPT = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
CHUNK_TEXT = "品牌🚀 chunk"
BODY = f"Intro α\n\nSection {CHUNK_TEXT} end"
CAPTURE_HASH = CaptureContentHash("sha256:" + "1" * 64)
PARSER_VERSION = "synthetic-parser-v1"
BODY_SENTINEL = "SYNTHETIC_CHUNK_SECRET_13A7"
GOLDEN_TEXT_DIGEST = (
    "01671e7384f137a997dc691d428e25253b3db2f55b5f8ffa84ba306103c34035"
)
GOLDEN_CHUNK_ID = (
    "chk:v1:sha256:"
    "1c7caf8b03b243f1399d8760c70c7922e5e297bff71b735ca06cfe7be8e0f8da"
)


def _canonical_url(raw_url: str):
    result = validate_and_canonicalize_url(
        LinkCandidate(
            raw_url=raw_url,
            source=LinkSource.CELL_HYPERLINK,
            asset_source_slot=AssetSourceSlot.ARTICLE,
            lineage=SourceLineage(
                spreadsheet_id="synthetic-spreadsheet",
                sheet_id=113,
                sheet_title="Synthetic Captured Chunks",
                sheet_hidden=False,
                source_row_index=6,
                source_column_index=7,
                source_fingerprint="sha256:synthetic-wp13-source",
                sync_batch_id="SYNTHETIC-WP13-BATCH",
            ),
            field_lineage=SourceFieldLineage(
                field_name="article",
                target_row_index=6,
                target_column_index=7,
                value_row_index=6,
                value_column_index=7,
                merge_anchor_row_index=None,
                merge_anchor_column_index=None,
                merge_range=None,
                inherited_from_merge=False,
                inheritance_reason=InheritanceReason.LOCAL,
            ),
        )
    )
    assert result.canonical_url is not None
    return result.canonical_url


def _lineage(
    *,
    sync_batch_id: str = "SYNTHETIC-WP13-BATCH",
    source_columns=None,
    source_ranges=None,
):
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=113,
        sheet_title="Synthetic Captured Chunks",
        source_row=7,
        source_columns=(
            {"source_record_id": "M", "article": "H"}
            if source_columns is None
            else source_columns
        ),
        source_ranges=(
            {"article": "H7"} if source_ranges is None else source_ranges
        ),
        source_fingerprint="sha256:synthetic-wp13-source",
        sync_batch_id=sync_batch_id,
    )


def _source_record(
    *,
    source_record_id: str = "MREC-0013",
    brand_id: str | None = "BRD-0013",
    sync_batch_id: str = "SYNTHETIC-WP13-BATCH",
) -> SourceRecord:
    approved = brand_id is not None
    return SourceRecord(
        source_record_id=SourceRecordId(source_record_id),
        brand_identity=BrandIdentityDecision(
            review_status=(
                ReviewStatus.APPROVED if approved else ReviewStatus.NEEDS_REVIEW
            ),
            brand_id=BrandId(brand_id) if brand_id is not None else None,
        ),
        interview_year=2026,
        source_name="Synthetic Source Record",
        sales_category_lv1=None,
        sales_category_lv2=None,
        tags=(),
        lifecycle_status=(
            LifecycleStatus.ACTIVE if approved else LifecycleStatus.NEEDS_REVIEW
        ),
        review_status=(
            ReviewStatus.APPROVED if approved else ReviewStatus.NEEDS_REVIEW
        ),
        publish_eligibility=(
            PublishEligibility.ELIGIBLE
            if approved
            else PublishEligibility.NEEDS_REVIEW
        ),
        source_lineage=_lineage(sync_batch_id=sync_batch_id),
    )


def _captured_payload(**overrides):
    payload = {
        "captured_content_id": CapturedContentId("capture-wp13-001"),
        "asset_key": ContentAssetKey(
            SourceRecordId("MREC-0013"),
            AssetType.ARTICLE,
        ),
        "metric_id": None,
        "evidence_relationship_id": None,
        "authority_role": AuthorityRole.PRIMARY_CONTENT,
        "source_url": _canonical_url("https://example.test/wp13-source"),
        "canonical_url": _canonical_url("https://example.test/wp13-canonical"),
        "source_domain": "example.test",
        "content_type": "text/html",
        "title": "Synthetic WP13 Article",
        "clean_body": BODY,
        "section_structure": (
            Section(heading="Section", text=f"{CHUNK_TEXT} end"),
        ),
        "capture_status": CaptureStatus.SUCCESS,
        "captured_at": CAPTURED_AT,
        "last_successful_capture_at": LAST_SUCCESS,
        "last_capture_attempt_at": LAST_ATTEMPT,
        "content_hash": str(CAPTURE_HASH),
        "parser_version": PARSER_VERSION,
        "source_http_metadata": SafeHttpMetadata(status_code=200),
        "previous_content_hash": None,
        "searchable": True,
        "source_lineage": _lineage(),
        "sync_batch_id": "SYNTHETIC-WP13-BATCH",
    }
    payload.update(overrides)
    return payload


def _captured(**overrides) -> CapturedContent:
    return CapturedContent(**_captured_payload(**overrides))


def _revision(
    *,
    captured_content_id: str = "capture-wp13-001",
    content_hash: CaptureContentHash = CAPTURE_HASH,
    parser_version: str = PARSER_VERSION,
) -> CaptureRevisionRef:
    return CaptureRevisionRef(
        captured_content_id=CapturedContentId(captured_content_id),
        content_hash=content_hash,
        parser_version=parser_version,
    )


def _span(**overrides) -> SyntheticChunkSpan:
    start = BODY.index(CHUNK_TEXT)
    payload = {
        "text": CHUNK_TEXT,
        "start": start,
        "end": start + len(CHUNK_TEXT),
        "section_anchor": SectionAnchor("section-alpha"),
        "section_heading": "Section",
        "ordinal": 0,
    }
    payload.update(overrides)
    return SyntheticChunkSpan(**payload)


def _stale_candidate(**overrides) -> StaleLkgCandidate:
    payload = {
        "revision_ref": _revision(),
        "capture_status": CaptureStatus.STALE,
        "captured_at": CAPTURED_AT,
        "last_successful_capture_at": LAST_SUCCESS,
        "last_capture_attempt_at": LAST_ATTEMPT,
        "previous_content_hash": None,
        "searchable": True,
        "freshness_policy_version": "synthetic-freshness-v1",
    }
    payload.update(overrides)
    return StaleLkgCandidate(**payload)


def _capture_policy_decision() -> CapturePolicyDecision:
    return CapturePolicyDecision(
        mode=CaptureMode.FULL_TEXT,
        reason=PolicyDecisionReason.SHOPLINE_OWNED,
        policy_version="synthetic-capture-policy-v1",
    )


def _lkg_input(**overrides) -> LkgEligibilityInput:
    missing = object()
    previous = overrides.pop("previous_success", missing)
    if previous is missing:
        previous = _captured(last_capture_attempt_at=LAST_SUCCESS)
    current_url = (
        previous.canonical_url
        if previous is not None
        else _canonical_url("https://example.test/wp13-canonical")
    )
    values = {
        "current_canonical_url": current_url,
        "previous_success": previous,
        "current_capture_policy": _capture_policy_decision(),
        "current_failure_category": FetchFailureCategory.TEMPORARY,
        "governance_allowed": True,
        "identity_reconciled": True,
        "freshness_policy": ApprovedLkgFreshnessPolicy(
            policy_version="synthetic-freshness-v1",
            max_age=timedelta(days=3),
        ),
        "current_attempt_at": LAST_ATTEMPT,
    }
    values.update(overrides)
    return LkgEligibilityInput(**values)


def _lkg_proof(**overrides) -> tuple[LkgEligibilityInput, LkgEligibilityResult]:
    input_value = _lkg_input(**overrides)
    return input_value, evaluate_lkg_reuse(input_value)


def _build_primary(**overrides) -> CapturedChunk:
    values = {
        "captured_content": _captured(),
        "revision_ref": _revision(),
        "span": _span(),
        "primary_source_record": _source_record(),
    }
    values.update(overrides)
    return build_captured_chunk(**values)


def _assert_error(code: str, function, /, *args, **kwargs):
    with pytest.raises(CapturedChunkError) as captured:
        function(*args, **kwargs)
    assert captured.value.code == code
    assert str(captured.value) == code


def _metadata_values(metadata: CapturedChunkMetadata):
    return {field.name: getattr(metadata, field.name) for field in fields(metadata)}


def test_public_surface_is_exact_and_digest_helpers_remain_private():
    assert captured_chunks.__all__ == [
        "CapturedChunk",
        "CapturedChunkError",
        "CapturedChunkId",
        "CapturedChunkMetadata",
        "CapturedChunkSourceLineage",
        "SectionAnchor",
        "SyntheticChunkSpan",
        "build_captured_chunk",
    ]
    assert not hasattr(captured_chunks, "ChunkTextDigest")


def test_span_uses_python_codepoint_offsets_and_half_open_body_binding():
    span = _span()

    assert BODY[span.start : span.end] == CHUNK_TEXT
    assert len(CHUNK_TEXT.encode("utf-8")) != len(CHUNK_TEXT)
    assert span.end - span.start == len(CHUNK_TEXT)
    assert _build_primary(span=span).text == CHUNK_TEXT


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("start", True, "SYNTHETIC_CHUNK_SPAN_START_INVALID"),
        ("end", False, "SYNTHETIC_CHUNK_SPAN_END_INVALID"),
        ("ordinal", True, "SYNTHETIC_CHUNK_SPAN_ORDINAL_INVALID"),
        ("start", -1, "SYNTHETIC_CHUNK_SPAN_RANGE_INVALID"),
        ("end", 0, "SYNTHETIC_CHUNK_SPAN_RANGE_INVALID"),
        ("ordinal", -1, "SYNTHETIC_CHUNK_SPAN_ORDINAL_INVALID"),
    ],
)
def test_span_rejects_bool_negative_and_empty_ranges(field, value, code):
    values = {
        "text": CHUNK_TEXT,
        "start": 0,
        "end": len(CHUNK_TEXT),
        "section_anchor": SectionAnchor("section-alpha"),
        "section_heading": None,
        "ordinal": 0,
    }
    values[field] = value
    _assert_error(code, SyntheticChunkSpan, **values)


@pytest.mark.parametrize("text", ["", "   ", None, 13])
def test_span_text_must_be_exact_nonblank_str(text):
    _assert_error(
        "SYNTHETIC_CHUNK_SPAN_TEXT_INVALID",
        SyntheticChunkSpan,
        text=text,
        start=0,
        end=1,
        section_anchor=SectionAnchor("section-alpha"),
        section_heading=None,
        ordinal=0,
    )


def test_builder_rejects_out_of_body_range_and_exact_text_mismatch():
    _assert_error(
        "CAPTURED_CHUNK_SPAN_OUT_OF_BOUNDS",
        _build_primary,
        span=_span(end=len(BODY) + 1),
    )
    _assert_error(
        "CAPTURED_CHUNK_SPAN_TEXT_MISMATCH",
        _build_primary,
        span=_span(text="品牌🚀 chunK"),
    )


@pytest.mark.parametrize(
    "value",
    ["", "   ", " edge", "edge ", "line\nbreak", "tab\tvalue", None, 1],
)
def test_section_anchor_is_strict_opaque_text(value):
    _assert_error("SECTION_ANCHOR_INVALID", SectionAnchor, value)


def test_section_anchor_preserves_exact_value_without_normalization():
    anchor = SectionAnchor("Section-Ä-opaque")

    assert str(anchor) == "Section-Ä-opaque"
    assert type(anchor) is SectionAnchor


@pytest.mark.parametrize(
    "heading",
    [
        "",
        "   ",
        " edge",
        "edge ",
        "line\nbreak",
        "line\u2028break",
        "tab\tvalue",
        1,
    ],
)
def test_optional_heading_is_exact_single_line_display_metadata(heading):
    _assert_error(
        "SYNTHETIC_CHUNK_SECTION_HEADING_INVALID",
        SyntheticChunkSpan,
        text="x",
        start=0,
        end=1,
        section_anchor=SectionAnchor("section-alpha"),
        section_heading=heading,
        ordinal=0,
    )


def test_optional_heading_accepts_none_and_does_not_change_identity():
    without_heading = _build_primary(span=_span(section_heading=None))
    changed_heading = _build_primary(span=_span(section_heading="Changed heading"))

    assert without_heading.metadata.section_heading is None
    assert without_heading.metadata.chunk_id == changed_heading.metadata.chunk_id


def test_chunk_text_digest_and_chunk_id_match_fixed_golden_vectors():
    digest = captured_chunks._compute_chunk_text_digest(CHUNK_TEXT)
    chunk = _build_primary()

    assert digest.hex() == GOLDEN_TEXT_DIGEST
    assert str(chunk.metadata.chunk_id) == GOLDEN_CHUNK_ID


def test_golden_vector_uses_utf8_byte_length_and_uint64_big_endian_framing():
    text_bytes = CHUNK_TEXT.encode("utf-8")
    expected_prefix = (
        b"MKA_CAPTURED_CHUNK_TEXT_V1\x00"
        + b"\x00\x00\x00\x00\x00\x00\x00\x10"
    )

    assert len(text_bytes) == 16
    assert captured_chunks._chunk_text_digest_payload(CHUNK_TEXT).startswith(
        expected_prefix
    )


@pytest.mark.parametrize(
    "value",
    [
        "1" * 64,
        "sha256:" + "1" * 64,
        "chk:v2:sha256:" + "1" * 64,
        "chk:v1:sha256:" + "A" * 64,
        "chk:v1:sha256:" + "1" * 63,
        " chk:v1:sha256:" + "1" * 64,
        "chk:v1:sha256:" + "1" * 64 + " ",
        None,
    ],
)
def test_captured_chunk_id_rejects_noncanonical_serialization(value):
    _assert_error("CAPTURED_CHUNK_ID_INVALID", CapturedChunkId, value)


def test_captured_chunk_id_is_strict_separate_namespace():
    value = CapturedChunkId("chk:v1:sha256:" + "1" * 64)

    assert type(value) is CapturedChunkId
    assert not isinstance(value, CaptureContentHash)
    with pytest.raises(Exception):
        CaptureContentHash(str(value))


def test_same_inputs_are_deterministic_and_ordinal_is_not_identity():
    first = _build_primary()
    replay = _build_primary()
    moved = _build_primary(span=_span(ordinal=99))

    assert first == replay
    assert first.metadata.chunk_id == moved.metadata.chunk_id
    assert first.metadata.chunk_ordinal == 0
    assert moved.metadata.chunk_ordinal == 99


def test_offsets_are_not_identity_when_text_anchor_and_revision_match():
    repeated_body = f"{CHUNK_TEXT} -- {CHUNK_TEXT}"
    first_start = repeated_body.index(CHUNK_TEXT)
    second_start = repeated_body.rindex(CHUNK_TEXT)
    captured = _captured(clean_body=repeated_body, section_structure=())
    first = _build_primary(
        captured_content=captured,
        span=_span(start=first_start, end=first_start + len(CHUNK_TEXT)),
    )
    second = _build_primary(
        captured_content=captured,
        span=_span(start=second_start, end=second_start + len(CHUNK_TEXT)),
    )

    assert first.metadata.chunk_id == second.metadata.chunk_id


def test_text_anchor_revision_parser_and_capture_id_each_change_identity():
    baseline = _build_primary().metadata.chunk_id
    other_text = "另一段 text"
    other_body = BODY.replace(CHUNK_TEXT, other_text)
    start = other_body.index(other_text)
    text_changed = _build_primary(
        captured_content=_captured(clean_body=other_body, section_structure=()),
        span=_span(text=other_text, start=start, end=start + len(other_text)),
    )
    anchor_changed = _build_primary(
        span=_span(section_anchor=SectionAnchor("section-beta"))
    )
    other_hash = CaptureContentHash("sha256:" + "2" * 64)
    revision_changed = _build_primary(
        captured_content=_captured(content_hash=str(other_hash)),
        revision_ref=_revision(content_hash=other_hash),
    )
    parser_changed = _build_primary(
        captured_content=_captured(parser_version="synthetic-parser-v2"),
        revision_ref=_revision(parser_version="synthetic-parser-v2"),
    )
    capture_changed = _build_primary(
        captured_content=_captured(
            captured_content_id=CapturedContentId("capture-wp13-002")
        ),
        revision_ref=_revision(captured_content_id="capture-wp13-002"),
    )

    assert len(
        {
            baseline,
            text_changed.metadata.chunk_id,
            anchor_changed.metadata.chunk_id,
            revision_changed.metadata.chunk_id,
            parser_changed.metadata.chunk_id,
            capture_changed.metadata.chunk_id,
        }
    ) == 6


@pytest.mark.parametrize(
    "revision_ref",
    [
        _revision(captured_content_id="capture-wp13-other"),
        _revision(content_hash=CaptureContentHash("sha256:" + "2" * 64)),
        _revision(parser_version="synthetic-parser-v2"),
    ],
)
def test_revision_ref_must_exactly_match_captured_content(revision_ref):
    _assert_error(
        "CAPTURED_CHUNK_REVISION_MISMATCH",
        _build_primary,
        revision_ref=revision_ref,
    )


@pytest.mark.parametrize(
    "proof_values",
    [
        pytest.param(lambda: (_lkg_proof()[0], None), id="input-only"),
        pytest.param(lambda: (None, _lkg_proof()[1]), id="result-only"),
        pytest.param(lambda: _lkg_proof(), id="input-and-result"),
    ],
)
def test_success_admission_rejects_stale_proof(proof_values):
    stale_lkg_input, stale_lkg_result = proof_values()
    _assert_error(
        "CAPTURED_CHUNK_STALE_PROOF_NOT_ALLOWED",
        _build_primary,
        stale_lkg_input=stale_lkg_input,
        stale_lkg_result=stale_lkg_result,
    )


def test_hand_built_stale_candidate_is_not_an_authorization_proof():
    with pytest.raises(TypeError):
        _build_primary(
            captured_content=_captured(capture_status=CaptureStatus.STALE),
            stale_lkg_candidate=_stale_candidate(),
        )


@pytest.mark.parametrize(
    ("input_value", "result", "code"),
    [
        (None, None, "CAPTURED_CHUNK_STALE_LKG_INPUT_REQUIRED"),
        (
            _lkg_proof()[0],
            None,
            "CAPTURED_CHUNK_STALE_LKG_RESULT_REQUIRED",
        ),
        (
            None,
            _lkg_proof()[1],
            "CAPTURED_CHUNK_STALE_LKG_INPUT_REQUIRED",
        ),
        (
            object(),
            _lkg_proof()[1],
            "CAPTURED_CHUNK_STALE_LKG_INPUT_REQUIRED",
        ),
        (
            _lkg_proof()[0],
            object(),
            "CAPTURED_CHUNK_STALE_LKG_RESULT_REQUIRED",
        ),
    ],
)
def test_stale_requires_exact_wp12_input_and_result(input_value, result, code):
    stale = _captured(capture_status=CaptureStatus.STALE)
    _assert_error(
        code,
        _build_primary,
        captured_content=stale,
        stale_lkg_input=input_value,
        stale_lkg_result=result,
    )


def test_valid_wp12_evaluator_result_builds_stale_chunk():
    input_value, result = _lkg_proof()
    chunk = _build_primary(
        captured_content=_captured(capture_status=CaptureStatus.STALE),
        stale_lkg_input=input_value,
        stale_lkg_result=result,
    )

    assert chunk.metadata.capture_status is CaptureStatus.STALE
    assert chunk.metadata.captured_at == CAPTURED_AT
    assert chunk.metadata.last_successful_capture_at == LAST_SUCCESS
    assert chunk.metadata.last_capture_attempt_at == LAST_ATTEMPT


def test_ineligible_wp12_result_is_rejected():
    input_value, result = _lkg_proof(governance_allowed=False)

    _assert_error(
        "CAPTURED_CHUNK_STALE_PROOF_INVALID",
        _build_primary,
        captured_content=_captured(capture_status=CaptureStatus.STALE),
        stale_lkg_input=input_value,
        stale_lkg_result=result,
    )


def test_stale_result_input_cross_target_replay_is_rejected():
    input_a, result_a = _lkg_proof()
    input_b = replace(
        input_a,
        current_capture_policy=CapturePolicyDecision(
            mode=CaptureMode.FULL_TEXT,
            reason=PolicyDecisionReason.SHOPLINE_OWNED,
            policy_version="synthetic-capture-policy-v2",
        ),
    )

    _assert_error(
        "CAPTURED_CHUNK_STALE_PROOF_INVALID",
        _build_primary,
        captured_content=_captured(capture_status=CaptureStatus.STALE),
        stale_lkg_input=input_b,
        stale_lkg_result=result_a,
    )


def test_stale_canonical_url_must_match_wp12_input():
    other_url = _canonical_url("https://example.test/wp13-other-canonical")
    previous = _captured(
        canonical_url=other_url,
        last_capture_attempt_at=LAST_SUCCESS,
    )
    input_value, result = _lkg_proof(previous_success=previous)

    _assert_error(
        "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        _build_primary,
        captured_content=_captured(capture_status=CaptureStatus.STALE),
        stale_lkg_input=input_value,
        stale_lkg_result=result,
    )


@pytest.mark.parametrize(
    ("captured_overrides", "revision_ref", "code"),
    [
        (
            {
                "content_hash": str(
                    CaptureContentHash("sha256:" + "2" * 64)
                )
            },
            _revision(content_hash=CaptureContentHash("sha256:" + "2" * 64)),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {"parser_version": "synthetic-parser-v2"},
            _revision(parser_version="synthetic-parser-v2"),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {"captured_content_id": CapturedContentId("capture-wp13-other")},
            _revision(captured_content_id="capture-wp13-other"),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {"captured_at": datetime(2026, 8, 1, 9, 0, tzinfo=UTC)},
            _revision(),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {
                "last_successful_capture_at": datetime(
                    2026, 8, 8, 9, 0, tzinfo=UTC
                )
            },
            _revision(),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {
                "last_capture_attempt_at": datetime(
                    2026, 8, 10, 9, 0, tzinfo=UTC
                )
            },
            _revision(),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {"previous_content_hash": "sha256:" + "3" * 64},
            _revision(),
            "CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH",
        ),
        (
            {"searchable": False},
            _revision(),
            "CAPTURED_CHUNK_NOT_SEARCHABLE",
        ),
    ],
)
def test_stale_candidate_must_match_every_frozen_parent_field(
    captured_overrides,
    revision_ref,
    code,
):
    input_value, result = _lkg_proof()
    _assert_error(
        code,
        _build_primary,
        captured_content=_captured(
            capture_status=CaptureStatus.STALE,
            **captured_overrides,
        ),
        revision_ref=revision_ref,
        stale_lkg_input=input_value,
        stale_lkg_result=result,
    )


@pytest.mark.parametrize(
    "status",
    [
        CaptureStatus.UNAVAILABLE,
        CaptureStatus.BLOCKED,
        CaptureStatus.METADATA_ONLY,
        CaptureStatus.NEEDS_REVIEW,
    ],
)
def test_non_full_text_statuses_are_rejected(status):
    last_attempt = LAST_ATTEMPT if status is CaptureStatus.UNAVAILABLE else None
    captured = _captured(
        capture_status=status,
        clean_body=None,
        section_structure=(),
        captured_at=None,
        content_hash=None,
        parser_version=None,
        searchable=False,
        last_capture_attempt_at=last_attempt,
    )
    _assert_error(
        "CAPTURED_CHUNK_STATUS_NOT_ALLOWED",
        build_captured_chunk,
        captured_content=captured,
        revision_ref=_revision(),
        span=_span(),
        primary_source_record=_source_record(),
    )


@pytest.mark.parametrize("status", [CaptureStatus.SUCCESS, CaptureStatus.STALE])
def test_searchable_false_full_text_is_rejected(status):
    captured = _captured(capture_status=status, searchable=False)
    _assert_error(
        "CAPTURED_CHUNK_NOT_SEARCHABLE",
        _build_primary,
        captured_content=captured,
    )


def test_primary_attribution_is_derived_from_parent_and_source_record():
    source_record = _source_record()
    chunk = _build_primary(primary_source_record=source_record)
    metadata = chunk.metadata

    assert metadata.authority_role is AuthorityRole.PRIMARY_CONTENT
    assert metadata.asset_key == ContentAssetKey(
        SourceRecordId("MREC-0013"), AssetType.ARTICLE
    )
    assert metadata.metric_id is None
    assert metadata.evidence_relationship_id is None
    assert metadata.source_record_id == SourceRecordId("MREC-0013")
    assert metadata.brand_id == BrandId("BRD-0013")


def test_primary_requires_matching_source_record_brand_and_batch():
    _assert_error(
        "CAPTURED_CHUNK_SOURCE_RECORD_MISMATCH",
        _build_primary,
        primary_source_record=_source_record(source_record_id="MREC-0099"),
    )
    _assert_error(
        "CAPTURED_CHUNK_PRIMARY_BRAND_REQUIRED",
        _build_primary,
        primary_source_record=_source_record(brand_id=None),
    )
    _assert_error(
        "CAPTURED_CHUNK_SOURCE_RECORD_BATCH_MISMATCH",
        _build_primary,
        primary_source_record=_source_record(sync_batch_id="SYNTHETIC-OTHER-BATCH"),
    )


def test_evidence_attribution_has_no_brand_or_source_record_guessing():
    captured = _captured(
        asset_key=None,
        metric_id=MetricId("MET-0013"),
        evidence_relationship_id=EvidenceRelationshipId("evidence-wp13-001"),
        authority_role=AuthorityRole.EVIDENCE,
    )
    chunk = build_captured_chunk(
        captured_content=captured,
        revision_ref=_revision(),
        span=_span(),
    )

    assert chunk.metadata.authority_role is AuthorityRole.EVIDENCE
    assert chunk.metadata.asset_key is None
    assert chunk.metadata.metric_id == MetricId("MET-0013")
    assert chunk.metadata.evidence_relationship_id == EvidenceRelationshipId(
        "evidence-wp13-001"
    )
    assert chunk.metadata.source_record_id is None
    assert chunk.metadata.brand_id is None


def test_evidence_rejects_primary_source_record_override():
    captured = _captured(
        asset_key=None,
        metric_id=MetricId("MET-0013"),
        evidence_relationship_id=EvidenceRelationshipId("evidence-wp13-001"),
        authority_role=AuthorityRole.EVIDENCE,
    )
    _assert_error(
        "CAPTURED_CHUNK_SOURCE_RECORD_NOT_ALLOWED",
        build_captured_chunk,
        captured_content=captured,
        revision_ref=_revision(),
        span=_span(),
        primary_source_record=_source_record(),
    )


def test_metadata_copies_optional_title_url_timestamps_and_lineage():
    captured = _captured(title=None)
    chunk = _build_primary(captured_content=captured)
    metadata = chunk.metadata

    assert metadata.title is None
    assert metadata.source_url == captured.source_url
    assert metadata.captured_at == captured.captured_at
    assert metadata.last_successful_capture_at == captured.last_successful_capture_at
    assert metadata.last_capture_attempt_at == captured.last_capture_attempt_at
    assert type(metadata.source_lineage) is captured_chunks.CapturedChunkSourceLineage
    assert metadata.source_lineage.spreadsheet_id_hash == (
        captured.source_lineage.spreadsheet_id_hash
    )
    assert metadata.source_lineage.sheet_id == captured.source_lineage.sheet_id
    assert metadata.source_lineage.sheet_title == captured.source_lineage.sheet_title
    assert metadata.source_lineage.source_row == captured.source_lineage.source_row
    assert metadata.source_lineage.source_fingerprint == (
        captured.source_lineage.source_fingerprint
    )
    assert metadata.revision_ref == _revision()
    assert metadata.captured_content_id == _revision().captured_content_id
    assert metadata.content_hash == _revision().content_hash
    assert metadata.parser_version == _revision().parser_version
    assert metadata.sync_batch_id == captured.source_lineage.sync_batch_id


def test_metadata_does_not_duplicate_revision_or_sync_authority_fields():
    names = [field.name for field in fields(CapturedChunkMetadata)]

    assert "captured_content_id" not in names
    assert "content_hash" not in names
    assert "parser_version" not in names
    assert "sync_batch_id" not in names
    assert names == [
        "chunk_id",
        "revision_ref",
        "asset_key",
        "metric_id",
        "evidence_relationship_id",
        "brand_id",
        "source_record_id",
        "authority_role",
        "title",
        "section_anchor",
        "section_heading",
        "chunk_ordinal",
        "source_url",
        "capture_status",
        "captured_at",
        "last_successful_capture_at",
        "last_capture_attempt_at",
        "searchable",
        "source_lineage",
    ]


def test_nested_dto_has_one_body_field_and_metadata_has_none():
    assert [field.name for field in fields(CapturedChunk)] == ["text", "metadata"]
    assert "text" not in {field.name for field in fields(CapturedChunkMetadata)}


def test_direct_canonical_constructors_require_builder():
    chunk = _build_primary()

    _assert_error(
        "CAPTURED_CHUNK_METADATA_REQUIRES_BUILDER",
        CapturedChunkMetadata,
        **_metadata_values(chunk.metadata),
    )
    _assert_error(
        "CAPTURED_CHUNK_REQUIRES_BUILDER",
        CapturedChunk,
        text=chunk.text,
        metadata=chunk.metadata,
    )


def test_dataclasses_replace_canonical_outputs_fails_closed():
    chunk = _build_primary()

    _assert_error(
        "CAPTURED_CHUNK_METADATA_REQUIRES_BUILDER",
        replace,
        chunk.metadata,
    )
    _assert_error(
        "CAPTURED_CHUNK_REQUIRES_BUILDER",
        replace,
        chunk,
    )


def test_internal_construction_preserves_authority_and_text_id_defenses():
    chunk = _build_primary()
    invalid_metadata = _metadata_values(chunk.metadata)
    invalid_metadata["authority_role"] = AuthorityRole.EVIDENCE

    _assert_error(
        "CAPTURED_CHUNK_EVIDENCE_ATTRIBUTION_INVALID",
        captured_chunks._create_captured_chunk_metadata,
        **invalid_metadata,
    )
    _assert_error(
        "CAPTURED_CHUNK_IDENTITY_MISMATCH",
        captured_chunks._create_captured_chunk,
        text="different",
        metadata=chunk.metadata,
    )
    with pytest.raises(CapturedChunkError):
        replace(_span(), ordinal=-1)


@pytest.mark.parametrize(
    ("mapping_name", "key", "value", "code"),
    [
        pytest.param(
            "source_columns",
            1,
            "value",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
            id="columns-mixed-key",
        ),
        pytest.param(
            "source_columns",
            "article",
            1,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
            id="columns-non-str-value",
        ),
        pytest.param(
            "source_columns",
            "   ",
            "value",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
            id="columns-blank-key",
        ),
        pytest.param(
            "source_columns",
            "article",
            "   ",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
            id="columns-blank-value",
        ),
        pytest.param(
            "source_ranges",
            1,
            "value",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
            id="ranges-mixed-key",
        ),
        pytest.param(
            "source_ranges",
            "article",
            1,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
            id="ranges-non-str-value",
        ),
        pytest.param(
            "source_ranges",
            "   ",
            "value",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
            id="ranges-blank-key",
        ),
        pytest.param(
            "source_ranges",
            "article",
            "   ",
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
            id="ranges-blank-value",
        ),
    ],
)
def test_builder_validates_mutated_lineage_entries_before_sorting(
    mapping_name,
    key,
    value,
    code,
):
    captured = _captured()
    mapping = getattr(captured.source_lineage, mapping_name)
    mapping[key] = value

    _assert_error(code, _build_primary, captured_content=captured)


def test_lineage_snapshot_is_sorted_and_insertion_order_independent():
    first = _captured(
        source_lineage=_lineage(
            source_columns={"source_record_id": "M", "article": "H"},
            source_ranges={"source_record_id": "M7", "article": "H7"},
        )
    )
    second = _captured(
        source_lineage=_lineage(
            source_columns={"article": "H", "source_record_id": "M"},
            source_ranges={"article": "H7", "source_record_id": "M7"},
        )
    )

    first_snapshot = _build_primary(captured_content=first).metadata.source_lineage
    second_snapshot = _build_primary(captured_content=second).metadata.source_lineage

    assert first_snapshot == second_snapshot
    assert first_snapshot.source_columns == (
        ("article", "H"),
        ("source_record_id", "M"),
    )
    assert first_snapshot.source_ranges == (
        ("article", "H7"),
        ("source_record_id", "M7"),
    )
    assert type(first_snapshot.source_columns) is tuple
    assert type(first_snapshot.source_ranges) is tuple
    assert all(type(item) is tuple for item in first_snapshot.source_columns)
    assert all(type(item) is tuple for item in first_snapshot.source_ranges)


def test_metadata_snapshots_caller_owned_lineage_mappings():
    source_columns = {"source_record_id": "M", "article": "H"}
    source_ranges = {"article": "H7"}
    lineage = _lineage(
        source_columns=source_columns,
        source_ranges=source_ranges,
    )
    captured = _captured(source_lineage=lineage)
    chunk = _build_primary(captured_content=captured)

    source_columns["article"] = "Y"
    source_ranges["article"] = "Y7"
    captured.source_lineage.source_columns["article"] = "Z"
    captured.source_lineage.source_ranges["article"] = "Z7"

    assert ("article", "H") in chunk.metadata.source_lineage.source_columns
    assert ("article", "H7") in chunk.metadata.source_lineage.source_ranges


def test_captured_lineage_snapshot_collections_cannot_be_mutated():
    snapshot = _build_primary().metadata.source_lineage

    with pytest.raises(TypeError):
        snapshot.source_columns[0] = ("article", "Z")
    with pytest.raises(AttributeError):
        snapshot.source_ranges.append(("article", "Z7"))


def test_captured_lineage_snapshot_is_direct_structural_dto():
    snapshot_type = captured_chunks.CapturedChunkSourceLineage
    snapshot = snapshot_type(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=113,
        sheet_title="Synthetic Captured Chunks",
        source_row=7,
        source_columns=(("article", "H"),),
        source_ranges=(("article", "H7"),),
        source_fingerprint="sha256:synthetic-wp13-source",
        sync_batch_id="SYNTHETIC-WP13-BATCH",
    )

    assert [field.name for field in fields(snapshot)] == [
        "spreadsheet_id_hash",
        "sheet_id",
        "sheet_title",
        "source_row",
        "source_columns",
        "source_ranges",
        "source_fingerprint",
        "sync_batch_id",
    ]
    assert snapshot.source_columns == (("article", "H"),)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("spreadsheet_id_hash", "   "),
        ("sheet_id", True),
        ("sheet_id", -1),
        ("sheet_title", None),
        ("source_row", 0),
        ("source_row", True),
        ("source_columns", {"article": "H"}),
        ("source_columns", [("article", "H")]),
        ("source_columns", (("article", ""),)),
        ("source_columns", (("article", 7),)),
        ("source_ranges", set()),
        ("source_ranges", (("article",),)),
        ("source_fingerprint", ""),
        ("sync_batch_id", "   "),
    ],
)
def test_captured_lineage_snapshot_validates_exact_structure(field, value):
    values = {
        "spreadsheet_id_hash": "sha256:synthetic-spreadsheet-id",
        "sheet_id": 113,
        "sheet_title": "Synthetic Captured Chunks",
        "source_row": 7,
        "source_columns": (("article", "H"),),
        "source_ranges": (("article", "H7"),),
        "source_fingerprint": "sha256:synthetic-wp13-source",
        "sync_batch_id": "SYNTHETIC-WP13-BATCH",
    }
    values[field] = value

    with pytest.raises(CapturedChunkError):
        captured_chunks.CapturedChunkSourceLineage(**values)


def test_lineage_mapping_payload_is_redacted_from_repr():
    captured = _captured(
        source_lineage=_lineage(
            source_columns={"article": BODY_SENTINEL},
            source_ranges={"article": BODY_SENTINEL},
        )
    )
    chunk = _build_primary(captured_content=captured)

    assert BODY_SENTINEL not in repr(chunk.metadata.source_lineage)
    assert BODY_SENTINEL not in repr(chunk.metadata)
    assert BODY_SENTINEL not in repr(chunk)


def test_repr_str_and_errors_redact_body_title_heading_and_url():
    secret_text = BODY_SENTINEL
    body = f"prefix {secret_text} suffix"
    start = body.index(secret_text)
    captured = _captured(
        clean_body=body,
        section_structure=(),
        title=f"{BODY_SENTINEL} title",
        source_url=_canonical_url(f"https://example.test/{BODY_SENTINEL}"),
    )
    span = _span(
        text=secret_text,
        start=start,
        end=start + len(secret_text),
        section_heading=f"{BODY_SENTINEL} heading",
    )
    chunk = _build_primary(captured_content=captured, span=span)

    with pytest.raises(CapturedChunkError) as captured_error:
        _build_primary(
            captured_content=captured,
            span=replace(span, text=f"{BODY_SENTINEL} mismatch"),
        )

    rendered = (
        repr(span),
        str(span),
        repr(chunk),
        str(chunk),
        repr(chunk.metadata),
        str(chunk.metadata),
        str(captured_error.value),
    )
    assert all(BODY_SENTINEL not in value for value in rendered)
    assert "<redacted>" in repr(chunk)


def test_builder_signature_and_module_have_no_splitter_index_or_markdown_surface():
    assert tuple(inspect.signature(build_captured_chunk).parameters) == (
        "captured_content",
        "revision_ref",
        "span",
        "primary_source_record",
        "stale_lkg_input",
        "stale_lkg_result",
    )
    source = inspect.getsource(captured_chunks)
    forbidden_imports = (
        "marketing_knowledge_agent.chunking",
        "marketing_knowledge_agent.indexing",
        "marketing_knowledge_agent.retrieval",
        "marketing_knowledge_agent.content_index",
        "frontmatter",
        "sqlite3",
    )
    assert all(name not in source for name in forbidden_imports)
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "split(" not in source
    assert re.search(r"\b(chunk_size|overlap|tokenizer|embedding)\b", source) is None


def test_builder_does_not_recompute_wp12_hash_or_evaluate_lkg():
    builder_source = inspect.getsource(build_captured_chunk)
    module_source = inspect.getsource(captured_chunks)

    assert "compute_capture_content_hash" not in builder_source
    assert "evaluate_lkg_reuse" not in module_source
    assert "compose_stale_lkg" in module_source
