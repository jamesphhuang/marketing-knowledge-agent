from __future__ import annotations

import hashlib
import inspect
import socket
import time
import urllib.request
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

import marketing_knowledge_agent.html_normalization as html_normalization
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    CanonicalSourceLineage,
    ContentAssetKey,
    SourceRecordId,
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
    ContentHashingError,
    LkgEligibilityInput,
    LkgEligibilityReason,
    LkgEligibilityResult,
    RevisionDecision,
    RevisionDisposition,
    RevisionReason,
    StaleLkgCandidate,
    compose_stale_lkg,
    compute_capture_content_hash,
    decide_capture_revision,
    evaluate_lkg_reuse,
)
from marketing_knowledge_agent.html_normalization import (
    HTML_NORMALIZER_VERSION,
    HtmlNormalizationResult,
    NormalizationStatus,
    NormalizedSection,
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
CURRENT_ATTEMPT = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
POLICY_VERSION = "synthetic-lkg-policy-v1"
BODY_SENTINEL = "SYNTHETIC_WP12_BODY_3D8F"
BINDING_SENTINEL = "SYNTHETIC_BINDING_SECRET_74C1"


def _normalization(
    body: str = "Synthetic normalized body",
    *,
    sections=None,
    parser_version: str = HTML_NORMALIZER_VERSION,
) -> HtmlNormalizationResult:
    normalized_sections = sections or (NormalizedSection(heading=None, text=body),)
    return HtmlNormalizationResult(
        status=NormalizationStatus.SUCCESS,
        title="Synthetic Article",
        clean_body=body,
        sections=normalized_sections,
        parser_version=parser_version,
        diagnostic_codes=(),
    )


def _canonical_url(raw_url: str):
    result = validate_and_canonicalize_url(
        LinkCandidate(
            raw_url=raw_url,
            source=LinkSource.CELL_HYPERLINK,
            asset_source_slot=AssetSourceSlot.ARTICLE,
            lineage=SourceLineage(
                spreadsheet_id="synthetic-spreadsheet",
                sheet_id=112,
                sheet_title="Synthetic Content Hashing",
                sheet_hidden=False,
                source_row_index=6,
                source_column_index=7,
                source_fingerprint="sha256:synthetic-wp12-source",
                sync_batch_id="SYNTHETIC-WP12-BATCH",
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


def _source_lineage() -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=112,
        sheet_title="Synthetic Content Hashing",
        source_row=7,
        source_columns={"source_record_id": "M", "article": "H"},
        source_ranges={"article": "H7"},
        source_fingerprint="sha256:synthetic-wp12-source",
        sync_batch_id="SYNTHETIC-WP12-BATCH",
    )


def _policy_decision(
    mode: CaptureMode = CaptureMode.FULL_TEXT,
    *,
    policy_version: str = "synthetic-capture-policy-v1",
):
    reasons = {
        CaptureMode.FULL_TEXT: PolicyDecisionReason.SHOPLINE_OWNED,
        CaptureMode.METADATA_ONLY: PolicyDecisionReason.NEEDS_POLICY,
        CaptureMode.UNSUPPORTED: PolicyDecisionReason.UNSUPPORTED,
        CaptureMode.BLOCKED: PolicyDecisionReason.POLICY_MISSING,
    }
    return CapturePolicyDecision(
        mode=mode,
        reason=reasons[mode],
        policy_version=policy_version,
    )


def _previous_success(**overrides) -> CapturedContent:
    normalization = _normalization(BODY_SENTINEL)
    payload = {
        "captured_content_id": CapturedContentId("capture-wp12-001"),
        "asset_key": ContentAssetKey(SourceRecordId("MREC-0012"), AssetType.ARTICLE),
        "metric_id": None,
        "evidence_relationship_id": None,
        "authority_role": AuthorityRole.PRIMARY_CONTENT,
        "source_url": _canonical_url("https://example.test/wp12-source"),
        "canonical_url": _canonical_url("https://example.test/wp12-canonical"),
        "source_domain": "example.test",
        "content_type": "text/html",
        "title": "Synthetic WP12 Article",
        "clean_body": normalization.clean_body,
        "section_structure": (
            Section(heading=None, text=normalization.sections[0].text),
        ),
        "capture_status": CaptureStatus.SUCCESS,
        "captured_at": CAPTURED_AT,
        "last_successful_capture_at": LAST_SUCCESS,
        "last_capture_attempt_at": LAST_SUCCESS,
        "content_hash": compute_capture_content_hash(normalization),
        "parser_version": normalization.parser_version,
        "source_http_metadata": SafeHttpMetadata(status_code=200),
        "previous_content_hash": "sha256:" + "1" * 64,
        "searchable": True,
        "source_lineage": _source_lineage(),
        "sync_batch_id": "SYNTHETIC-WP12-BATCH",
    }
    payload.update(overrides)
    return CapturedContent(**payload)


def _bodyless_previous(status: CaptureStatus) -> CapturedContent:
    return _previous_success(
        capture_status=status,
        clean_body=None,
        section_structure=(),
        captured_at=None,
        content_hash=None,
        parser_version=None,
        searchable=False,
        last_capture_attempt_at=(
            LAST_SUCCESS if status is CaptureStatus.UNAVAILABLE else None
        ),
    )


def _freshness(
    max_age: timedelta = timedelta(days=2),
    *,
    policy_version: str = POLICY_VERSION,
):
    return ApprovedLkgFreshnessPolicy(
        policy_version=policy_version,
        max_age=max_age,
    )


def _eligibility_input(**overrides) -> LkgEligibilityInput:
    missing = object()
    previous = overrides.pop("previous_success", missing)
    if previous is missing:
        previous = _previous_success()
    current_url = (
        previous.canonical_url
        if previous is not None
        else _canonical_url("https://example.test/wp12-canonical")
    )
    values = {
        "current_canonical_url": current_url,
        "previous_success": previous,
        "current_capture_policy": _policy_decision(),
        "current_failure_category": FetchFailureCategory.TEMPORARY,
        "governance_allowed": True,
        "identity_reconciled": True,
        "freshness_policy": _freshness(),
        "current_attempt_at": CURRENT_ATTEMPT,
    }
    values.update(overrides)
    return LkgEligibilityInput(**values)


def test_capture_hash_matches_exact_frozen_golden_and_typed_format():
    content_hash = compute_capture_content_hash(_normalization())

    assert type(content_hash) is CaptureContentHash
    assert content_hash == (
        "sha256:9284af9336c437505cfd666b789451ec465a5f7b369cd5f824cd679304810169"
    )
    assert len(content_hash) == len("sha256:") + 64


def test_capture_hash_multibyte_framing_uses_utf8_byte_lengths():
    result = _normalization("台灣🙂")
    parser_bytes = result.parser_version.encode("utf-8")
    body_bytes = result.clean_body.encode("utf-8")
    independently_framed = (
        b"MKA_CAPTURE_CONTENT_HASH_V1\x00"
        + len(parser_bytes).to_bytes(8, "big")
        + parser_bytes
        + len(body_bytes).to_bytes(8, "big")
        + body_bytes
    )

    assert compute_capture_content_hash(result) == (
        "sha256:" + hashlib.sha256(independently_framed).hexdigest()
    )


def test_capture_hash_is_deterministic_and_body_changes_it():
    original = _normalization("Synthetic normalized body")
    changed = _normalization("Synthetic normalized body changed")

    assert compute_capture_content_hash(original) == compute_capture_content_hash(original)
    assert compute_capture_content_hash(original) != compute_capture_content_hash(changed)


def test_parser_version_participates_in_hash(monkeypatch):
    original = _normalization()
    future_version = "html-normalizer-v2"
    monkeypatch.setattr(html_normalization, "HTML_NORMALIZER_VERSION", future_version)
    upgraded = _normalization(parser_version=future_version)

    assert original.clean_body == upgraded.clean_body
    assert compute_capture_content_hash(original) != compute_capture_content_hash(upgraded)


def test_sections_do_not_independently_participate_in_hash():
    one_section = _normalization(
        "First\n\nSecond",
        sections=(NormalizedSection(heading=None, text="First\n\nSecond"),),
    )
    two_sections = _normalization(
        "First\n\nSecond",
        sections=(
            NormalizedSection(heading=None, text="First"),
            NormalizedSection(heading=None, text="Second"),
        ),
    )

    assert one_section.sections != two_sections.sections
    assert compute_capture_content_hash(one_section) == compute_capture_content_hash(
        two_sections
    )


@pytest.mark.parametrize(
    "invalid_result",
    [
        "<p>raw html</p>",
        b"<p>raw html</p>",
        {"clean_body": "not-a-result"},
        None,
        HtmlNormalizationResult(
            status=NormalizationStatus.NO_MEANINGFUL_CONTENT,
            title=None,
            clean_body=None,
            sections=(),
            parser_version=HTML_NORMALIZER_VERSION,
            diagnostic_codes=("NO_MEANINGFUL_CONTENT",),
        ),
        HtmlNormalizationResult(
            status=NormalizationStatus.NEEDS_REVIEW,
            title=None,
            clean_body=None,
            sections=(),
            parser_version=HTML_NORMALIZER_VERSION,
            diagnostic_codes=("NEEDS_REVIEW",),
        ),
    ],
)
def test_capture_hash_accepts_only_successful_wp11_result(invalid_result):
    with pytest.raises(ContentHashingError):
        compute_capture_content_hash(invalid_result)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0" * 64,
        "sha256:" + "0" * 63,
        "sha256:" + "0" * 65,
        "sha256:" + "A" * 64,
        "sha512:" + "0" * 64,
        " sha256:" + "0" * 64,
        "sha256:" + "0" * 64 + " ",
        "sha256:" + "0" * 63 + "\n",
        "sha256:" + "0" * 63 + "\x00",
        None,
        b"sha256:" + b"0" * 64,
        1,
    ],
)
def test_capture_hash_wrapper_rejects_noncanonical_serialization(value):
    with pytest.raises(ContentHashingError, match="CAPTURE_CONTENT_HASH_INVALID"):
        CaptureContentHash(value)


def test_plain_source_fingerprint_cannot_enter_capture_revision_namespace():
    source_fingerprint = "sha256:" + "4" * 64

    with pytest.raises(ContentHashingError, match="CAPTURE_REVISION_HASH_INVALID"):
        CaptureRevisionRef(
            captured_content_id=CapturedContentId("capture-wp12-001"),
            content_hash=source_fingerprint,
            parser_version=HTML_NORMALIZER_VERSION,
        )


def test_hash_boundary_never_reflects_body_payload_in_errors():
    with pytest.raises(ContentHashingError) as captured:
        compute_capture_content_hash(BODY_SENTINEL)

    assert BODY_SENTINEL not in str(captured.value)
    assert BODY_SENTINEL not in repr(captured.value)


def test_capture_hash_api_has_no_incidental_identity_inputs():
    assert tuple(inspect.signature(compute_capture_content_hash).parameters) == ("result",)
    for forbidden in (
        "captured_at",
        "last_successful_capture_at",
        "last_capture_attempt_at",
        "source_url",
        "canonical_url",
        "http_status",
        "etag",
        "source_fingerprint",
        "revision_id",
    ):
        with pytest.raises(TypeError):
            compute_capture_content_hash(_normalization(), **{forbidden: "synthetic"})


def _revision(
    *,
    content_hash: str = "sha256:" + "2" * 64,
    parser_version: str = "html-normalizer-v1",
    captured_content_id: str = "capture-wp12-001",
) -> CaptureRevisionRef:
    return CaptureRevisionRef(
        captured_content_id=CapturedContentId(captured_content_id),
        content_hash=CaptureContentHash(content_hash),
        parser_version=parser_version,
    )


def test_revision_contract_has_exact_frozen_taxonomy_and_ref_fields():
    assert [item.value for item in RevisionDisposition] == [
        "same_content",
        "new_revision",
    ]
    assert [item.value for item in RevisionReason] == [
        "same_content",
        "first_success",
        "body_changed",
        "parser_version_changed",
    ]
    assert [field.name for field in fields(CaptureRevisionRef)] == [
        "captured_content_id",
        "content_hash",
        "parser_version",
    ]


def test_first_success_is_a_new_revision():
    assert decide_capture_revision(None, _revision()) == RevisionDecision(
        disposition=RevisionDisposition.NEW_REVISION,
        reason=RevisionReason.FIRST_SUCCESS,
    )


def test_same_parser_and_hash_is_same_content():
    revision = _revision()

    assert decide_capture_revision(revision, revision) == RevisionDecision(
        disposition=RevisionDisposition.SAME_CONTENT,
        reason=RevisionReason.SAME_CONTENT,
    )


def test_same_parser_and_different_hash_is_body_changed():
    previous = _revision(content_hash="sha256:" + "2" * 64)
    current = _revision(content_hash="sha256:" + "3" * 64)

    assert decide_capture_revision(previous, current) == RevisionDecision(
        disposition=RevisionDisposition.NEW_REVISION,
        reason=RevisionReason.BODY_CHANGED,
    )


def test_parser_drift_is_not_reported_as_body_changed():
    previous = _revision(parser_version="html-normalizer-v1")
    current = _revision(
        parser_version="html-normalizer-v2",
        content_hash=previous.content_hash,
    )

    assert decide_capture_revision(previous, current) == RevisionDecision(
        disposition=RevisionDisposition.NEW_REVISION,
        reason=RevisionReason.PARSER_VERSION_CHANGED,
    )


def test_parser_drift_precedes_a_different_hash():
    previous = _revision(
        parser_version="html-normalizer-v1",
        content_hash="sha256:" + "2" * 64,
    )
    current = _revision(
        parser_version="html-normalizer-v2",
        content_hash="sha256:" + "3" * 64,
    )

    assert decide_capture_revision(previous, current) == RevisionDecision(
        disposition=RevisionDisposition.NEW_REVISION,
        reason=RevisionReason.PARSER_VERSION_CHANGED,
    )


def test_revision_comparison_rejects_different_logical_capture_ids():
    with pytest.raises(ContentHashingError, match="CAPTURE_REVISION_LINEAGE_MISMATCH"):
        decide_capture_revision(
            _revision(captured_content_id="capture-wp12-001"),
            _revision(captured_content_id="capture-wp12-002"),
        )


def test_lkg_reason_taxonomy_is_exact_and_stable():
    assert [reason.value for reason in LkgEligibilityReason] == [
        "eligible",
        "policy_not_full_text",
        "governance_rejected",
        "identity_reconciliation_failed",
        "no_previous_success",
        "url_changed",
        "failure_not_temporary",
        "freshness_policy_missing",
        "timestamp_missing",
        "freshness_expired",
    ]


def test_lkg_input_and_freshness_policy_have_exact_frozen_fields():
    assert [field.name for field in fields(ApprovedLkgFreshnessPolicy)] == [
        "policy_version",
        "max_age",
    ]
    assert [field.name for field in fields(LkgEligibilityInput)] == [
        "current_canonical_url",
        "previous_success",
        "current_capture_policy",
        "current_failure_category",
        "governance_allowed",
        "identity_reconciled",
        "freshness_policy",
        "current_attempt_at",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        (CaptureMode.METADATA_ONLY, LkgEligibilityReason.POLICY_NOT_FULL_TEXT),
        (CaptureMode.UNSUPPORTED, LkgEligibilityReason.POLICY_NOT_FULL_TEXT),
        (CaptureMode.BLOCKED, LkgEligibilityReason.POLICY_NOT_FULL_TEXT),
    ],
)
def test_lkg_requires_full_text_capture_policy(mode, expected_reason):
    result = evaluate_lkg_reuse(
        _eligibility_input(current_capture_policy=_policy_decision(mode))
    )

    assert result.eligible is False
    assert result.reason is expected_reason


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"governance_allowed": False}, LkgEligibilityReason.GOVERNANCE_REJECTED),
        (
            {"identity_reconciled": False},
            LkgEligibilityReason.IDENTITY_RECONCILIATION_FAILED,
        ),
        ({"previous_success": None}, LkgEligibilityReason.NO_PREVIOUS_SUCCESS),
        (
            {"previous_success": _bodyless_previous(CaptureStatus.BLOCKED)},
            LkgEligibilityReason.NO_PREVIOUS_SUCCESS,
        ),
        (
            {"current_canonical_url": _canonical_url("https://example.test/changed")},
            LkgEligibilityReason.URL_CHANGED,
        ),
        (
            {"current_failure_category": FetchFailureCategory.NON_TEMPORARY},
            LkgEligibilityReason.FAILURE_NOT_TEMPORARY,
        ),
        ({"current_failure_category": None}, LkgEligibilityReason.FAILURE_NOT_TEMPORARY),
        ({"freshness_policy": None}, LkgEligibilityReason.FRESHNESS_POLICY_MISSING),
        ({"current_attempt_at": None}, LkgEligibilityReason.TIMESTAMP_MISSING),
        (
            {"freshness_policy": _freshness(timedelta(hours=1))},
            LkgEligibilityReason.FRESHNESS_EXPIRED,
        ),
    ],
)
def test_lkg_rejection_gates(overrides, expected_reason):
    result = evaluate_lkg_reuse(_eligibility_input(**overrides))

    assert result.eligible is False
    assert result.reason is expected_reason


def test_lkg_gate_precedence_is_frozen():
    result = evaluate_lkg_reuse(
        _eligibility_input(
            current_capture_policy=_policy_decision(CaptureMode.BLOCKED),
            governance_allowed=False,
            identity_reconciled=False,
            previous_success=None,
            current_canonical_url=_canonical_url("https://example.test/changed"),
            current_failure_category=FetchFailureCategory.NON_TEMPORARY,
            freshness_policy=None,
            current_attempt_at=None,
        )
    )

    assert result.reason is LkgEligibilityReason.POLICY_NOT_FULL_TEXT


def test_missing_freshness_precedes_missing_timestamp():
    result = evaluate_lkg_reuse(
        _eligibility_input(freshness_policy=None, current_attempt_at=None)
    )

    assert result.reason is LkgEligibilityReason.FRESHNESS_POLICY_MISSING


def test_freshness_exact_boundary_is_eligible():
    previous = _previous_success()
    input_value = _eligibility_input(
        previous_success=previous,
        freshness_policy=_freshness(CURRENT_ATTEMPT - LAST_SUCCESS),
    )

    result = evaluate_lkg_reuse(input_value)

    assert result.eligible is True
    assert result.reason is LkgEligibilityReason.ELIGIBLE
    assert result.freshness_policy_version == POLICY_VERSION


def test_clock_regression_is_validation_error_before_gate_precedence():
    with pytest.raises(ContentHashingError, match="LKG_TIME_ORDER_INVALID"):
        evaluate_lkg_reuse(
            _eligibility_input(
                current_capture_policy=_policy_decision(CaptureMode.BLOCKED),
                current_attempt_at=LAST_SUCCESS - timedelta(microseconds=1),
            )
        )


@pytest.mark.parametrize(
    "policy_version",
    ["", " leading", "trailing ", "line\nbreak", 123],
)
def test_freshness_policy_version_is_strict(policy_version):
    with pytest.raises(ContentHashingError, match="LKG_FRESHNESS_POLICY_VERSION_INVALID"):
        ApprovedLkgFreshnessPolicy(
            policy_version=policy_version,
            max_age=timedelta(days=1),
        )


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(microseconds=-1), 1, None])
def test_freshness_policy_requires_exact_positive_timedelta(max_age):
    with pytest.raises(ContentHashingError, match="LKG_FRESHNESS_MAX_AGE_INVALID"):
        ApprovedLkgFreshnessPolicy(
            policy_version=POLICY_VERSION,
            max_age=max_age,
        )


def test_lkg_input_requires_aware_caller_supplied_attempt_time():
    with pytest.raises(ContentHashingError, match="LKG_ATTEMPT_TIMESTAMP_AWARE_REQUIRED"):
        _eligibility_input(current_attempt_at=datetime(2026, 8, 10, 8, 0))


def test_lkg_input_rejects_raw_failure_or_url_facts():
    with pytest.raises(ContentHashingError, match="LKG_FAILURE_CATEGORY_INVALID"):
        _eligibility_input(current_failure_category=429)
    with pytest.raises(ContentHashingError, match="LKG_CANONICAL_URL_INVALID"):
        _eligibility_input(current_canonical_url="https://example.test/raw")


@pytest.mark.parametrize("field", ["governance_allowed", "identity_reconciled"])
@pytest.mark.parametrize("value", [0, 1])
def test_lkg_input_boolean_fields_are_exact(field, value):
    with pytest.raises(ContentHashingError):
        replace(_eligibility_input(), **{field: value})


@pytest.mark.parametrize(
    "status",
    [
        CaptureStatus.STALE,
        CaptureStatus.UNAVAILABLE,
        CaptureStatus.BLOCKED,
        CaptureStatus.METADATA_ONLY,
        CaptureStatus.NEEDS_REVIEW,
    ],
)
def test_every_non_success_previous_status_is_rejected(status):
    previous = (
        _previous_success(capture_status=CaptureStatus.STALE)
        if status is CaptureStatus.STALE
        else _bodyless_previous(status)
    )

    result = evaluate_lkg_reuse(_eligibility_input(previous_success=previous))

    assert result.reason is LkgEligibilityReason.NO_PREVIOUS_SUCCESS


def test_compose_stale_references_previous_revision_without_copying_body():
    previous = _previous_success(searchable=False)
    input_value = _eligibility_input(previous_success=previous)
    result = evaluate_lkg_reuse(input_value)

    candidate = compose_stale_lkg(input_value, result)

    assert candidate == StaleLkgCandidate(
        revision_ref=CaptureRevisionRef(
            captured_content_id=previous.captured_content_id,
            content_hash=CaptureContentHash(previous.content_hash),
            parser_version=previous.parser_version,
        ),
        capture_status=CaptureStatus.STALE,
        captured_at=previous.captured_at,
        last_successful_capture_at=previous.last_successful_capture_at,
        last_capture_attempt_at=CURRENT_ATTEMPT,
        previous_content_hash=previous.previous_content_hash,
        searchable=False,
        freshness_policy_version=POLICY_VERSION,
    )
    assert [field.name for field in fields(StaleLkgCandidate)] == [
        "revision_ref",
        "capture_status",
        "captured_at",
        "last_successful_capture_at",
        "last_capture_attempt_at",
        "previous_content_hash",
        "searchable",
        "freshness_policy_version",
    ]
    assert not hasattr(candidate, "clean_body")
    assert not hasattr(candidate, "section_structure")


def test_compose_stale_rejects_ineligible_result_without_redeciding():
    input_value = _eligibility_input(governance_allowed=False)
    result = evaluate_lkg_reuse(input_value)

    with pytest.raises(ContentHashingError, match="LKG_RESULT_NOT_ELIGIBLE"):
        compose_stale_lkg(input_value, result)


def test_direct_eligible_result_without_evaluator_binding_is_rejected():
    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_REQUIRED",
    ):
        LkgEligibilityResult(
            eligible=True,
            reason=LkgEligibilityReason.ELIGIBLE,
            freshness_policy_version=POLICY_VERSION,
        )


def _changed_replay_input(change: str) -> LkgEligibilityInput:
    if change == "previous_captured_content_id":
        return _eligibility_input(
            previous_success=_previous_success(
                captured_content_id=CapturedContentId("capture-wp12-002")
            )
        )
    if change == "previous_content_hash":
        return _eligibility_input(
            previous_success=_previous_success(content_hash="sha256:" + "5" * 64)
        )
    if change == "previous_parser_version":
        return _eligibility_input(
            previous_success=_previous_success(parser_version="html-normalizer-v2")
        )
    if change == "previous_canonical_url":
        return _eligibility_input(
            previous_success=_previous_success(
                canonical_url=_canonical_url("https://example.test/other-previous")
            )
        )
    if change == "previous_captured_at":
        return _eligibility_input(
            previous_success=_previous_success(
                captured_at=CAPTURED_AT - timedelta(hours=1)
            )
        )
    if change == "previous_last_successful_capture_at":
        return _eligibility_input(
            previous_success=_previous_success(
                last_successful_capture_at=LAST_SUCCESS - timedelta(hours=1)
            )
        )
    if change == "previous_previous_content_hash":
        return _eligibility_input(
            previous_success=_previous_success(
                previous_content_hash="sha256:" + "6" * 64
            )
        )
    if change == "previous_searchable":
        return _eligibility_input(previous_success=_previous_success(searchable=False))
    if change == "current_canonical_url":
        return _eligibility_input(
            current_canonical_url=_canonical_url("https://example.test/changed-current")
        )
    if change == "capture_policy_mode":
        return _eligibility_input(
            current_capture_policy=_policy_decision(CaptureMode.METADATA_ONLY)
        )
    if change == "capture_policy_version":
        return _eligibility_input(
            current_capture_policy=_policy_decision(
                policy_version="synthetic-capture-policy-v2"
            )
        )
    if change == "failure_category":
        return _eligibility_input(
            current_failure_category=FetchFailureCategory.NON_TEMPORARY
        )
    if change == "governance_allowed":
        return _eligibility_input(governance_allowed=False)
    if change == "identity_reconciled":
        return _eligibility_input(identity_reconciled=False)
    if change == "freshness_policy_version":
        return _eligibility_input(
            freshness_policy=_freshness(policy_version="synthetic-lkg-policy-v2")
        )
    if change == "freshness_max_age":
        return _eligibility_input(freshness_policy=_freshness(timedelta(days=3)))
    if change == "current_attempt_at":
        return _eligibility_input(
            current_attempt_at=CURRENT_ATTEMPT + timedelta(minutes=1)
        )
    raise AssertionError("unknown synthetic replay case")


@pytest.mark.parametrize(
    "change",
    [
        "previous_captured_content_id",
        "previous_content_hash",
        "previous_parser_version",
        "previous_canonical_url",
        "previous_captured_at",
        "previous_last_successful_capture_at",
        "previous_previous_content_hash",
        "previous_searchable",
        "current_canonical_url",
        "capture_policy_mode",
        "capture_policy_version",
        "failure_category",
        "governance_allowed",
        "identity_reconciled",
        "freshness_policy_version",
        "freshness_max_age",
        "current_attempt_at",
    ],
)
def test_eligible_result_cannot_be_replayed_across_context(change):
    input_a = _eligibility_input()
    result_a = evaluate_lkg_reuse(input_a)
    input_b = _changed_replay_input(change)

    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_MISMATCH",
    ):
        compose_stale_lkg(input_b, result_a)


def test_replay_target_can_be_independently_policy_rejected():
    input_a = _eligibility_input()
    result_a = evaluate_lkg_reuse(input_a)
    input_b = _changed_replay_input("capture_policy_mode")

    assert evaluate_lkg_reuse(input_b).reason is (
        LkgEligibilityReason.POLICY_NOT_FULL_TEXT
    )
    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_MISMATCH",
    ):
        compose_stale_lkg(input_b, result_a)


def test_eligible_result_binding_and_replace_invariants_are_closed():
    result = evaluate_lkg_reuse(_eligibility_input())

    assert result._binding is not None
    assert replace(result) == result
    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_MISMATCH",
    ):
        replace(result, freshness_policy_version="synthetic-lkg-policy-other")
    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_REQUIRED",
    ):
        replace(result, _binding=None)
    with pytest.raises(ContentHashingError):
        replace(result, eligible=False)
    with pytest.raises(ContentHashingError):
        replace(result, reason=LkgEligibilityReason.URL_CHANGED)
    with pytest.raises(
        ContentHashingError,
        match="LKG_ELIGIBILITY_BINDING_NOT_ALLOWED",
    ):
        replace(
            result,
            eligible=False,
            reason=LkgEligibilityReason.URL_CHANGED,
        )


def test_rejected_result_has_no_reusable_binding():
    result = evaluate_lkg_reuse(
        _eligibility_input(current_capture_policy=_policy_decision(CaptureMode.BLOCKED))
    )

    assert result.eligible is False
    assert result._binding is None


def test_binding_is_private_redacted_and_contains_no_body_or_sections():
    import marketing_knowledge_agent.content_hashing as content_hashing

    result = evaluate_lkg_reuse(_eligibility_input())
    binding = result._binding
    assert binding is not None
    binding_fields = {field.name for field in fields(binding)}

    assert "_LkgEligibilityBinding" not in content_hashing.__all__
    assert "clean_body" not in binding_fields
    assert "section_structure" not in binding_fields
    assert BODY_SENTINEL not in repr(binding)
    assert "example.test" not in repr(binding)


def test_binding_result_and_mismatch_error_do_not_leak_bound_payload():
    bound_url = _canonical_url(f"https://example.test/{BINDING_SENTINEL}")
    input_a = _eligibility_input(
        current_canonical_url=bound_url,
        previous_success=_previous_success(canonical_url=bound_url),
        freshness_policy=_freshness(policy_version=BINDING_SENTINEL),
    )
    result_a = evaluate_lkg_reuse(input_a)

    assert BINDING_SENTINEL not in repr(result_a._binding)
    assert BINDING_SENTINEL not in repr(result_a)
    assert BINDING_SENTINEL not in str(result_a)
    with pytest.raises(ContentHashingError) as captured:
        compose_stale_lkg(
            replace(
                input_a,
                current_capture_policy=_policy_decision(CaptureMode.METADATA_ONLY),
            ),
            result_a,
        )
    assert captured.value.code == "LKG_ELIGIBILITY_BINDING_MISMATCH"
    assert BINDING_SENTINEL not in str(captured.value)


def test_eligibility_result_carries_no_body_or_revision_mutation():
    assert [
        field.name
        for field in fields(LkgEligibilityResult)
        if not field.name.startswith("_")
    ] == [
        "eligible",
        "reason",
        "freshness_policy_version",
    ]


def test_lkg_repr_never_copies_previous_body_or_current_url():
    input_value = _eligibility_input()
    candidate = compose_stale_lkg(input_value, evaluate_lkg_reuse(input_value))

    assert BODY_SENTINEL not in repr(input_value)
    assert input_value.current_canonical_url.value not in repr(input_value)
    assert BODY_SENTINEL not in repr(candidate)


@pytest.mark.parametrize(
    "update",
    [
        {"revision_ref": "not-a-revision-ref"},
        {"capture_status": CaptureStatus.SUCCESS},
        {"captured_at": datetime(2026, 8, 1, 8, 0)},
        {"last_capture_attempt_at": LAST_SUCCESS - timedelta(microseconds=1)},
        {"previous_content_hash": " "},
        {"searchable": 1},
        {"freshness_policy_version": " "},
    ],
)
def test_stale_candidate_replace_revalidates_structural_invariants(update):
    input_value = _eligibility_input()
    candidate = compose_stale_lkg(input_value, evaluate_lkg_reuse(input_value))

    with pytest.raises(ContentHashingError):
        replace(candidate, **update)


def test_stale_candidate_direct_constructor_rejects_invalid_structure():
    input_value = _eligibility_input()
    candidate = compose_stale_lkg(input_value, evaluate_lkg_reuse(input_value))
    values = {
        field.name: getattr(candidate, field.name)
        for field in fields(StaleLkgCandidate)
    }
    values["capture_status"] = CaptureStatus.SUCCESS

    with pytest.raises(
        ContentHashingError,
        match="STALE_LKG_CAPTURE_STATUS_INVALID",
    ):
        StaleLkgCandidate(**values)


def test_compose_does_not_call_evaluator_or_duplicate_gate_logic():
    source = inspect.getsource(compose_stale_lkg)

    assert "evaluate_lkg_reuse" not in source
    assert "CaptureMode" not in source
    assert "governance_allowed" not in source
    assert "identity_reconciled" not in source
    assert "current_failure_category" not in source


def test_wp12_functions_have_no_network_filesystem_retry_or_clock_side_effects(
    monkeypatch,
):
    def unexpected(*args, **kwargs):
        raise AssertionError("WP12 boundary side effect")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(time, "sleep", unexpected)

    content_hash = compute_capture_content_hash(_normalization())
    revision = _revision(content_hash=content_hash)
    assert decide_capture_revision(None, revision).reason is RevisionReason.FIRST_SUCCESS
    input_value = _eligibility_input()
    result = evaluate_lkg_reuse(input_value)
    assert compose_stale_lkg(input_value, result).capture_status is CaptureStatus.STALE


def test_production_module_has_no_forbidden_boundary_dependency_or_current_clock():
    import marketing_knowledge_agent.content_hashing as content_hashing

    source = inspect.getsource(content_hashing).lower()
    forbidden = {
        "requests",
        "httpx",
        "urllib.request",
        "aiohttp",
        "socket",
        "getaddrinfo",
        "datetime.now",
        "datetime.utcnow",
        "time.time",
        "sleep(",
        "retry",
        "backoff",
        "sqlite",
        "obsidian",
        "vector",
        "slack",
        "release_pointer",
        "source_fingerprint",
    }

    assert all(term not in source for term in forbidden)
