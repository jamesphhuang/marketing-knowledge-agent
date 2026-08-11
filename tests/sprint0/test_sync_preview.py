from __future__ import annotations

import builtins
import json
import socket
from dataclasses import fields, replace
from datetime import date

import pytest

import marketing_knowledge_agent.sync_preview as sync_preview
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    BrandId,
    CanonicalSourceLineage,
    ContentAssetKey,
    ExposureChannel,
    LifecycleStatus,
    MetricId,
    PublishEligibility,
    ReviewStatus,
    SourceRecordId,
)
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.google_normalization import (
    ExcludedSourceRef,
    ExclusionReason,
    MetricSourceCells,
    minimize_public_metric_source,
)
from marketing_knowledge_agent.link_resolution import (
    AssetResolution,
    AssetResolutionStatus,
    AssetSourceSlot,
    LinkCandidate,
    LinkSource,
    resolve_content_asset,
)
from marketing_knowledge_agent.sync_preview import (
    PreviewBuildContext,
    PreviewContractError,
    PreviewDiffDecision,
    PreviewField,
    PreviewItem,
    PreviewReason,
    PreviewReasonDomain,
    PreviewStatus,
    PreviewSummary,
    RedactedValidationIssueInput,
    ValidationIssue,
    ValidationReasonCode,
    ValidationSeverity,
    build_preview,
    render_preview_json,
    render_preview_markdown,
)
from marketing_knowledge_agent.url_safety import (
    URLRejectionCode,
    validate_and_canonicalize_url,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
ORAL_SENTINEL = "ORAL_SENTINEL_7f31"
URL_SENTINEL = "url-sentinel-9d2c"
TOKEN_SENTINEL = "TOKEN_SENTINEL_18BC"
NOTE_SENTINEL = "NOTE_SENTINEL_4A71"
HTML_SENTINEL = "HTML_SENTINEL_92EF"
CONSTRUCTOR_SENTINEL = "TOKEN_SENTINEL_FINAL_91A7"
STRUCTURAL_CONSTRUCTION_ERROR = "PREVIEW_STRUCTURAL_CONSTRUCTION_INVALID"

_STATUS_VALUES = (
    "create",
    "update",
    "archive",
    "restore",
    "incomplete",
    "excluded",
    "needs_review",
    "unchanged",
)
_SEVERITY_VALUES = (
    "blocking_error",
    "needs_review",
    "excluded",
    "warning",
)
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


def _context(**changes) -> PreviewBuildContext:
    values = {
        "source_fingerprint": HASH_A,
        "policy_version": "synthetic-policy-v1",
        "normalized_hash": HASH_B,
    }
    values.update(changes)
    return PreviewBuildContext(**values)


def _key(
    number: int = 1,
    asset_type: AssetType = AssetType.ARTICLE,
) -> ContentAssetKey:
    return ContentAssetKey(SourceRecordId(f"MREC-{number:04d}"), asset_type)


def _lineage(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
) -> tuple[SourceLineage, SourceFieldLineage]:
    column = _SLOT_TO_COLUMN[slot]
    lineage = SourceLineage(
        spreadsheet_id="synthetic-spreadsheet-wp15",
        sheet_id=sheet_id,
        sheet_title="Synthetic Content Assets",
        sheet_hidden=False,
        source_row_index=row,
        source_column_index=column,
        source_fingerprint=HASH_A,
        sync_batch_id="SYNTHETIC-WP15-BATCH",
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


def _resolution(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    number: int = 1,
    title: str | None = "Synthetic Asset",
    candidates: tuple[LinkCandidate, ...] = (),
) -> AssetResolution:
    lineage, field_lineage = _lineage(slot=slot, row=row, sheet_id=sheet_id)
    results = tuple(validate_and_canonicalize_url(item) for item in candidates)
    result = resolve_content_asset(
        asset_key=_key(number, _SLOT_TO_TYPE[slot]),
        brand_id=BrandId("BRD-0001"),
        normalized_title=title,
        lineage=lineage,
        field_lineage=field_lineage,
        candidates=candidates,
        validation_results=results,
    )
    assert result is not None
    return result


def _resolved(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    number: int = 1,
    extra_candidates: tuple[LinkCandidate, ...] = (),
    title: str = "Synthetic Asset",
) -> AssetResolution:
    safe = _candidate(
        f"https://{URL_SENTINEL}.example.test/story-{number}",
        slot=slot,
        row=row,
        sheet_id=sheet_id,
    )
    return _resolution(
        slot=slot,
        row=row,
        sheet_id=sheet_id,
        number=number,
        title=title,
        candidates=(safe, *extra_candidates),
    )


def _incomplete(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    number: int = 1,
    rejected: tuple[LinkCandidate, ...] = (),
) -> AssetResolution:
    return _resolution(
        slot=slot,
        row=row,
        sheet_id=sheet_id,
        number=number,
        title="Synthetic Missing URL",
        candidates=rejected,
    )


def _needs_review(
    *,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    number: int = 1,
) -> AssetResolution:
    candidates = (
        _candidate(
            f"https://example.test/first-{number}",
            slot=slot,
            row=row,
            sheet_id=sheet_id,
        ),
        _candidate(
            f"https://example.test/second-{number}",
            source=LinkSource.LITERAL_TEXT,
            slot=slot,
            row=row,
            sheet_id=sheet_id,
        ),
    )
    return _resolution(
        slot=slot,
        row=row,
        sheet_id=sheet_id,
        number=number,
        candidates=candidates,
    )


def _sensitive_candidate(
    *,
    source: LinkSource = LinkSource.LITERAL_TEXT,
    slot: AssetSourceSlot = AssetSourceSlot.ARTICLE,
    row: int = 6,
    sheet_id: int = 108,
    ordinal: int = 1,
) -> LinkCandidate:
    return _candidate(
        f"https://example.test/private-{ordinal}?token={TOKEN_SENTINEL}",
        source=source,
        slot=slot,
        row=row,
        sheet_id=sheet_id,
    )


def _diff(
    resolution: AssetResolution,
    status: PreviewStatus = PreviewStatus.CREATE,
) -> PreviewDiffDecision:
    return PreviewDiffDecision(
        asset_key=resolution.asset_key,
        status=status,
        sheet_id=resolution.lineage.sheet_id,
        source_row=resolution.lineage.source_row_index + 1,
    )


def _archive(
    *,
    number: int = 1,
    asset_type: AssetType = AssetType.ARTICLE,
    sheet_id: int = 108,
    source_row: int = 7,
) -> PreviewDiffDecision:
    return PreviewDiffDecision(
        asset_key=_key(number, asset_type),
        status=PreviewStatus.ARCHIVE,
        sheet_id=sheet_id,
        source_row=source_row,
    )


def _excluded(
    *,
    metric_id: MetricId | None = MetricId("MET-0001"),
    sheet_id: int = 101,
    source_row: int = 7,
    digest_char: str = "c",
) -> ExcludedSourceRef:
    return ExcludedSourceRef(
        sheet_id=sheet_id,
        source_row=source_row,
        metric_id=metric_id,
        reason=ExclusionReason.ORAL_ONLY,
        source_digest="sha256:" + digest_char * 64,
    )


def _oral_exclusion() -> ExcludedSourceRef:
    lineage = CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=101,
        sheet_title=f"Synthetic {ORAL_SENTINEL}",
        source_row=7,
        source_columns={ORAL_SENTINEL: "C"},
        source_ranges={NOTE_SENTINEL: "C7"},
        source_fingerprint=HASH_A,
        sync_batch_id="SYNTHETIC-WP15-BATCH",
    )
    source = MetricSourceCells(
        metric_id=MetricId("MET-0001"),
        metric_type=TOKEN_SENTINEL,
        indicator=HTML_SENTINEL,
        approved_statement=ORAL_SENTINEL,
        note=f"不留文字紀錄 {NOTE_SENTINEL}",
        maintenance_updated_at=date(2026, 8, 11),
        evidence_urls=(f"https://example.test/{URL_SENTINEL}",),
        channel_cells=(),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        can_quote_externally=False,
        source_lineage=lineage,
    )
    result = minimize_public_metric_source(source)
    assert type(result) is ExcludedSourceRef
    return result


def _validation(
    *,
    severity: ValidationSeverity = ValidationSeverity.BLOCKING_ERROR,
    code: str = "SCHEMA_MISMATCH",
    sheet_id: int = 120,
    source_row: int = 9,
    field: PreviewField = PreviewField.PUBLIC_METRIC,
    asset_key: ContentAssetKey | None = None,
    metric_id: MetricId | None = None,
) -> RedactedValidationIssueInput:
    return RedactedValidationIssueInput(
        severity=severity,
        reason_code=ValidationReasonCode(code),
        sheet_id=sheet_id,
        source_row=source_row,
        field=field,
        asset_key=asset_key,
        metric_id=metric_id,
    )


def _build(
    *,
    exclusions: tuple[ExcludedSourceRef, ...] = (),
    asset_resolutions: tuple[AssetResolution, ...] = (),
    diff_decisions: tuple[PreviewDiffDecision, ...] = (),
    validation_issues: tuple[RedactedValidationIssueInput, ...] = (),
) -> PreviewSummary:
    return build_preview(
        _context(),
        exclusions,
        asset_resolutions,
        diff_decisions,
        validation_issues,
    )


def _assert_contract_error(function, *args, **kwargs) -> PreviewContractError:
    with pytest.raises(PreviewContractError) as exc_info:
        function(*args, **kwargs)
    assert exc_info.value.code == str(exc_info.value)
    assert exc_info.value.code.isascii()
    assert exc_info.value.code.replace("_", "A").isalnum()
    return exc_info.value


def test_public_api_is_exact():
    assert sync_preview.__all__ == [
        "PreviewContractError",
        "PreviewStatus",
        "ValidationSeverity",
        "PreviewField",
        "PreviewReasonDomain",
        "ValidationReasonCode",
        "PreviewReason",
        "PreviewBuildContext",
        "PreviewDiffDecision",
        "RedactedValidationIssueInput",
        "ValidationIssue",
        "PreviewItem",
        "PreviewSummary",
        "build_preview",
        "render_preview_json",
        "render_preview_markdown",
    ]


def test_enum_contracts_are_exact_and_separate():
    assert tuple(item.value for item in PreviewStatus) == _STATUS_VALUES
    assert tuple(item.value for item in ValidationSeverity) == _SEVERITY_VALUES
    assert tuple(item.value for item in PreviewField) == (
        "public_metric",
        "article",
        "video",
        "podcast",
        "news",
    )
    assert tuple(item.value for item in PreviewReasonDomain) == (
        "exclusion",
        "asset_resolution",
        "url_rejection",
        "validation",
    )
    assert PreviewStatus.NEEDS_REVIEW is not ValidationSeverity.NEEDS_REVIEW


@pytest.mark.parametrize(
    "value",
    ["A", "SCHEMA_MISMATCH", "A" * 64, "A0_B9"],
)
def test_validation_reason_code_accepts_exact_safe_grammar(value):
    code = ValidationReasonCode(value)
    assert type(code) is ValidationReasonCode
    assert str(code) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "lowercase",
        "A" * 65,
        " BAD",
        "BAD ",
        "BAD CODE",
        "BAD:CODE",
        "BAD/CODE",
        "BAD|CODE",
        "BAD`CODE",
        "BAD<CODE>",
        "BAD\nCODE",
        "BAD\tCODE",
        "\ud800",
        b"BAD",
        True,
        1,
    ],
)
def test_validation_reason_code_rejects_invalid_or_non_exact_text(value):
    _assert_contract_error(ValidationReasonCode, value)


def test_validation_reason_code_rejects_str_subclass():
    class TextSubclass(str):
        pass

    _assert_contract_error(ValidationReasonCode, TextSubclass("SCHEMA_MISMATCH"))


@pytest.mark.parametrize(
    ("domain", "code"),
    [
        (PreviewReasonDomain.EXCLUSION, "oral_only"),
        (PreviewReasonDomain.ASSET_RESOLUTION, "incomplete"),
        (PreviewReasonDomain.ASSET_RESOLUTION, "needs_review"),
        (
            PreviewReasonDomain.URL_REJECTION,
            URLRejectionCode.SENSITIVE_QUERY.value,
        ),
        (
            PreviewReasonDomain.VALIDATION,
            ValidationReasonCode("SCHEMA_MISMATCH"),
        ),
    ],
)
def test_preview_reason_accepts_only_domain_specific_codes(domain, code):
    reason = PreviewReason(domain=domain, code=code)
    assert reason.domain is domain
    assert type(reason.code) is str


@pytest.mark.parametrize(
    ("domain", "code"),
    [
        (PreviewReasonDomain.EXCLUSION, "written"),
        (PreviewReasonDomain.ASSET_RESOLUTION, "resolved_candidate"),
        (PreviewReasonDomain.URL_REJECTION, "UNKNOWN_URL_REASON"),
        (PreviewReasonDomain.VALIDATION, "anything"),
        (PreviewReasonDomain.VALIDATION, "SCHEMA_MISMATCH"),
    ],
)
def test_preview_reason_rejects_wrong_domain_code_combinations(domain, code):
    _assert_contract_error(PreviewReason, domain=domain, code=code)


@pytest.mark.parametrize("field", ("source_fingerprint", "normalized_hash"))
@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "sha512:" + "a" * 64,
        "a" * 64,
        True,
        b"sha256:" + b"a" * 64,
    ],
)
def test_build_context_rejects_invalid_hash_references(field, value):
    values = {
        "source_fingerprint": HASH_A,
        "policy_version": "policy-v1",
        "normalized_hash": HASH_B,
    }
    values[field] = value
    _assert_contract_error(PreviewBuildContext, **values)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " bad",
        "bad ",
        "bad policy",
        "bad/policy",
        "bad:policy",
        "bad|policy",
        "bad`policy",
        "bad<policy>",
        "bad\npolicy",
        "\ud800",
        "a" * 65,
        True,
    ],
)
def test_build_context_rejects_unsafe_policy_versions(value):
    _assert_contract_error(
        PreviewBuildContext,
        source_fingerprint=HASH_A,
        policy_version=value,
        normalized_hash=HASH_B,
    )


@pytest.mark.parametrize(
    "status",
    (
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.ARCHIVE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    ),
)
def test_diff_decision_accepts_only_lifecycle_statuses(status):
    decision = PreviewDiffDecision(
        asset_key=_key(), status=status, sheet_id=1, source_row=1
    )
    assert decision.status is status


@pytest.mark.parametrize(
    "status",
    (
        PreviewStatus.INCOMPLETE,
        PreviewStatus.EXCLUDED,
        PreviewStatus.NEEDS_REVIEW,
    ),
)
def test_diff_decision_rejects_non_lifecycle_statuses(status):
    _assert_contract_error(
        PreviewDiffDecision,
        asset_key=_key(),
        status=status,
        sheet_id=1,
        source_row=1,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("sheet_id", True), ("sheet_id", -1), ("source_row", True), ("source_row", 0)],
)
def test_diff_decision_rejects_unsafe_coordinates(field, value):
    values = {
        "asset_key": _key(),
        "status": PreviewStatus.CREATE,
        "sheet_id": 1,
        "source_row": 1,
    }
    values[field] = value
    _assert_contract_error(PreviewDiffDecision, **values)


def test_redacted_validation_input_identity_matrix_and_blocking_are_valid():
    row_level = _validation()
    metric = _validation(metric_id=MetricId("MET-0001"))
    asset = _validation(
        field=PreviewField.ARTICLE,
        asset_key=_key(),
    )
    assert row_level.asset_key is row_level.metric_id is None
    assert metric.metric_id == MetricId("MET-0001")
    assert asset.asset_key == _key()
    assert row_level.severity is ValidationSeverity.BLOCKING_ERROR


def test_redacted_validation_input_rejects_two_identities():
    _assert_contract_error(
        RedactedValidationIssueInput,
        severity=ValidationSeverity.WARNING,
        reason_code=ValidationReasonCode("IDENTITY_CONFLICT"),
        sheet_id=1,
        source_row=1,
        field=PreviewField.ARTICLE,
        asset_key=_key(),
        metric_id=MetricId("MET-0001"),
    )


def test_oral_exclusion_maps_to_one_item_and_one_issue_without_digest():
    exclusion = _excluded()
    summary = _build(exclusions=(exclusion,))
    item = summary.items[0]
    issue = summary.issues[0]

    assert item.status is PreviewStatus.EXCLUDED
    assert item.field is PreviewField.PUBLIC_METRIC
    assert item.sheet_id == exclusion.sheet_id
    assert item.source_row == exclusion.source_row
    assert item.metric_id == exclusion.metric_id
    assert item.asset_key is None
    assert item.candidate_count == item.rejected_count == 0
    assert item.reasons == (
        PreviewReason(PreviewReasonDomain.EXCLUSION, "oral_only"),
    )
    assert issue.severity is ValidationSeverity.EXCLUDED
    assert issue.reason == item.reasons[0]
    rendered = repr(summary) + render_preview_json(summary) + render_preview_markdown(summary)
    assert exclusion.source_digest not in rendered
    assert "source_digest" not in rendered


def test_wrong_exclusion_reason_fails_closed_with_stable_error():
    values = {
        "sheet_id": 101,
        "source_row": 7,
        "metric_id": MetricId("MET-0001"),
        "reason": "wrong_reason",
        "source_digest": "sha256:" + "c" * 64,
    }
    if hasattr(ExcludedSourceRef, "model_construct"):
        malformed = ExcludedSourceRef.model_construct(**values)
    else:  # pragma: no cover - exercised only with Pydantic 1.x
        malformed = ExcludedSourceRef.construct(**values)

    error = _assert_contract_error(_build, exclusions=(malformed,))
    assert "wrong_reason" not in str(error)


def test_oral_sentinels_are_removed_before_preview_and_never_reappear():
    exclusion = _oral_exclusion()
    summary = _build(exclusions=(exclusion,))
    observed = (
        repr(summary.items[0]),
        repr(summary.issues[0]),
        repr(summary),
        render_preview_json(summary),
        render_preview_markdown(summary),
    )
    for sentinel in (
        ORAL_SENTINEL,
        URL_SENTINEL,
        TOKEN_SENTINEL,
        NOTE_SENTINEL,
        HTML_SENTINEL,
        exclusion.source_digest,
    ):
        assert all(sentinel not in value for value in observed)


@pytest.mark.parametrize(
    ("slot", "field"),
    [
        (AssetSourceSlot.ARTICLE, PreviewField.ARTICLE),
        (AssetSourceSlot.VIDEO, PreviewField.VIDEO),
        (AssetSourceSlot.PODCAST, PreviewField.PODCAST),
        (AssetSourceSlot.NEWS, PreviewField.NEWS),
    ],
)
def test_incomplete_assets_map_field_location_item_and_issue(slot, field):
    resolution = _incomplete(slot=slot, row=0)
    summary = _build(asset_resolutions=(resolution,))
    item = summary.items[0]
    issue = summary.issues[0]

    assert item.status is PreviewStatus.INCOMPLETE
    assert item.field is field
    assert item.source_row == 1
    assert item.asset_key == resolution.asset_key
    assert item.metric_id is None
    assert item.reasons == (
        PreviewReason(PreviewReasonDomain.ASSET_RESOLUTION, "incomplete"),
    )
    assert issue.severity is ValidationSeverity.NEEDS_REVIEW
    assert issue.reason == item.reasons[0]


def test_needs_review_asset_maps_to_item_and_issue_without_winner():
    resolution = _needs_review()
    summary = _build(asset_resolutions=(resolution,))
    item = summary.items[0]

    assert item.status is PreviewStatus.NEEDS_REVIEW
    assert item.reasons == (
        PreviewReason(PreviewReasonDomain.ASSET_RESOLUTION, "needs_review"),
    )
    assert item.candidate_count == 2
    assert summary.issues[0].severity is ValidationSeverity.NEEDS_REVIEW
    assert not hasattr(item, "winner")
    assert not hasattr(item, "selected_url")


def test_resolved_candidate_requires_matching_diff_decision():
    resolution = _resolved()
    _assert_contract_error(_build, asset_resolutions=(resolution,))


@pytest.mark.parametrize(
    "status",
    (
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    ),
)
def test_resolved_candidate_uses_upstream_diff_status(status):
    resolution = _resolved()
    summary = _build(
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution, status),),
    )
    item = summary.items[0]
    assert item.status is status
    assert item.reasons == ()
    assert item.candidate_count == 1
    assert item.rejected_count == 0


def test_resolved_candidate_rejects_archive_decision():
    resolution = _resolved()
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution, PreviewStatus.ARCHIVE),),
    )


def test_archive_requires_explicit_decision_and_no_current_resolution():
    decision = _archive()
    summary = _build(diff_decisions=(decision,))
    item = summary.items[0]
    assert item.status is PreviewStatus.ARCHIVE
    assert item.asset_key == decision.asset_key
    assert item.field is PreviewField.ARTICLE
    assert item.reasons == ()
    assert item.candidate_count == item.rejected_count == 0


def test_no_resolution_and_no_archive_produces_no_item():
    assert _build().items == ()


def test_archive_with_current_resolution_is_rejected():
    resolution = _incomplete()
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(_archive(),),
    )


@pytest.mark.parametrize(
    "status",
    (
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    ),
)
def test_non_archive_diff_without_resolved_candidate_is_rejected(status):
    decision = PreviewDiffDecision(
        asset_key=_key(), status=status, sheet_id=1, source_row=1
    )
    _assert_contract_error(_build, diff_decisions=(decision,))


@pytest.mark.parametrize("factory", (_incomplete, _needs_review))
def test_incomplete_or_needs_review_rejects_diff_decision(factory):
    resolution = factory()
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution),),
    )


def test_duplicate_diff_and_resolution_keys_fail_closed():
    resolution = _resolved()
    decision = _diff(resolution)
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(decision, decision),
    )
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution, resolution),
        diff_decisions=(decision,),
    )


@pytest.mark.parametrize(("field", "delta"), [("sheet_id", 1), ("source_row", 1)])
def test_resolved_diff_location_must_match(field, delta):
    resolution = _resolved()
    decision = _diff(resolution)
    mismatched = replace(decision, **{field: getattr(decision, field) + delta})
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(mismatched,),
    )


def test_url_rejections_project_unique_codes_and_keep_occurrence_count():
    rejected = (
        _sensitive_candidate(source=LinkSource.LITERAL_TEXT, ordinal=1),
        _sensitive_candidate(source=LinkSource.HYPERLINK_FORMULA, ordinal=2),
    )
    resolution = _resolved(extra_candidates=rejected, title=f"title-{URL_SENTINEL}")
    summary = _build(
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution),),
    )
    item = summary.items[0]
    issues = summary.issues

    assert item.candidate_count == 1
    assert item.rejected_count == 2
    assert len(issues) == 1
    assert issues[0].severity is ValidationSeverity.WARNING
    assert issues[0].reason == PreviewReason(
        PreviewReasonDomain.URL_REJECTION,
        URLRejectionCode.SENSITIVE_QUERY.value,
    )
    rendered = repr(item) + repr(issues[0]) + render_preview_json(summary) + render_preview_markdown(summary)
    assert URL_SENTINEL not in rendered
    assert TOKEN_SENTINEL not in rendered
    assert "canonical_url" not in rendered
    assert "provenance" not in rendered
    assert "title-" not in rendered


@pytest.mark.parametrize("factory", (_incomplete, _needs_review))
def test_url_rejection_on_unresolved_asset_is_needs_review(factory):
    rejected = (_sensitive_candidate(),)
    if factory is _incomplete:
        resolution = factory(rejected=rejected)
    else:
        resolution = _resolution(title=None, candidates=rejected)
    summary = _build(asset_resolutions=(resolution,))
    rejection_issues = [
        issue
        for issue in summary.issues
        if issue.reason.domain is PreviewReasonDomain.URL_REJECTION
    ]
    assert len(rejection_issues) == 1
    assert rejection_issues[0].severity is ValidationSeverity.NEEDS_REVIEW


@pytest.mark.parametrize("severity", tuple(ValidationSeverity))
def test_external_redacted_issue_maps_to_issue_only_and_renders(severity):
    upstream = _validation(severity=severity)
    summary = _build(validation_issues=(upstream,))
    assert summary.items == ()
    assert len(summary.issues) == 1
    assert summary.issues[0].severity is severity
    assert summary.issues[0].reason == PreviewReason(
        PreviewReasonDomain.VALIDATION,
        ValidationReasonCode("SCHEMA_MISMATCH"),
    )
    assert "SCHEMA_MISMATCH" in render_preview_json(summary)
    assert "SCHEMA_MISMATCH" in render_preview_markdown(summary)


def test_issue_dedupe_is_exact_and_different_reasons_remain():
    first = _validation(code="SCHEMA_MISMATCH")
    second = _validation(code="IDENTITY_CONFLICT")
    summary = _build(validation_issues=(first, first, second))
    assert len(summary.issues) == 2
    assert {issue.reason.code for issue in summary.issues} == {
        "SCHEMA_MISMATCH",
        "IDENTITY_CONFLICT",
    }


def test_identical_oral_items_and_reasons_are_canonically_deduped():
    first = _excluded(digest_char="c")
    second = _excluded(digest_char="d")
    summary = _build(exclusions=(first, second))
    assert len(summary.items) == 1
    assert len(summary.items[0].reasons) == 1
    assert len(summary.issues) == 1


def test_same_asset_item_conflicts_fail_closed_instead_of_last_one_wins():
    resolution = _resolved()
    _assert_contract_error(
        _build,
        asset_resolutions=(resolution,),
        diff_decisions=(_archive(),),
    )


def test_counts_include_every_enum_and_conserve_items_and_issues():
    resolved = _resolved(number=1)
    incomplete = _incomplete(number=2, row=7)
    review = _needs_review(number=3, row=8)
    summary = _build(
        exclusions=(_excluded(),),
        asset_resolutions=(review, resolved, incomplete),
        diff_decisions=(_diff(resolved, PreviewStatus.UPDATE), _archive(number=4)),
        validation_issues=(_validation(severity=ValidationSeverity.WARNING),),
    )
    assert tuple(status.value for status, _ in summary.status_counts) == _STATUS_VALUES
    assert tuple(severity.value for severity, _ in summary.severity_counts) == _SEVERITY_VALUES
    assert sum(count for _, count in summary.status_counts) == len(summary.items)
    assert sum(count for _, count in summary.severity_counts) == len(summary.issues)
    status_counts = dict(summary.status_counts)
    severity_counts = dict(summary.severity_counts)
    assert status_counts[PreviewStatus.UPDATE] == 1
    assert status_counts[PreviewStatus.ARCHIVE] == 1
    assert status_counts[PreviewStatus.INCOMPLETE] == 1
    assert status_counts[PreviewStatus.EXCLUDED] == 1
    assert status_counts[PreviewStatus.NEEDS_REVIEW] == 1
    assert severity_counts[ValidationSeverity.WARNING] == 1


def test_item_issue_and_reason_order_are_input_order_independent():
    create = _resolved(number=1, row=9)
    update = _resolved(number=2, row=2)
    validations = (
        _validation(severity=ValidationSeverity.WARNING, code="Z_WARNING"),
        _validation(severity=ValidationSeverity.BLOCKING_ERROR, code="A_BLOCK"),
    )
    first = _build(
        exclusions=(_excluded(),),
        asset_resolutions=(update, create),
        diff_decisions=(
            _diff(update, PreviewStatus.UPDATE),
            _diff(create, PreviewStatus.CREATE),
        ),
        validation_issues=validations,
    )
    second = _build(
        exclusions=(_excluded(),),
        asset_resolutions=(create, update),
        diff_decisions=(
            _diff(create, PreviewStatus.CREATE),
            _diff(update, PreviewStatus.UPDATE),
        ),
        validation_issues=tuple(reversed(validations)),
    )
    assert first == second
    assert render_preview_json(first) == render_preview_json(second)
    assert render_preview_markdown(first) == render_preview_markdown(second)
    assert [item.status for item in first.items] == [
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.EXCLUDED,
    ]
    assert first.issues[0].severity is ValidationSeverity.BLOCKING_ERROR


@pytest.mark.parametrize(
    "argument",
    ("exclusions", "asset_resolutions", "diff_decisions", "validation_issues"),
)
@pytest.mark.parametrize("bad", ([], {}, set(), iter(())))
def test_builder_requires_exact_tuples(argument, bad):
    values = {
        "context": _context(),
        "exclusions": (),
        "asset_resolutions": (),
        "diff_decisions": (),
        "validation_issues": (),
    }
    values[argument] = bad
    _assert_contract_error(build_preview, **values)


@pytest.mark.parametrize(
    ("argument", "element"),
    [
        ("exclusions", {}),
        ("asset_resolutions", _excluded()),
        ("diff_decisions", object()),
        ("validation_issues", "SCHEMA_MISMATCH"),
    ],
)
def test_builder_rejects_wrong_elements_before_map_or_sort(argument, element):
    values = {
        "context": _context(),
        "exclusions": (),
        "asset_resolutions": (),
        "diff_decisions": (),
        "validation_issues": (),
    }
    values[argument] = (element,)
    error = _assert_contract_error(build_preview, **values)
    assert "dict" not in str(error)
    assert "object" not in str(error)


def test_builder_requires_exact_context_type():
    _assert_contract_error(build_preview, {}, (), (), (), ())


def _canonical_values(summary: PreviewSummary):
    item = summary.items[0]
    issue = summary.issues[0]
    return item, issue, {
        "schema_version": summary.schema_version,
        "source_fingerprint": summary.source_fingerprint,
        "policy_version": summary.policy_version,
        "normalized_hash": summary.normalized_hash,
        "status_counts": summary.status_counts,
        "severity_counts": summary.severity_counts,
        "items": summary.items,
        "issues": summary.issues,
    }


def test_canonical_outputs_require_builder_and_replace_fails_closed():
    summary = _build(exclusions=(_excluded(),))
    item, issue, summary_values = _canonical_values(summary)
    item_values = {field.name: getattr(item, field.name) for field in fields(item)}
    issue_values = {field.name: getattr(issue, field.name) for field in fields(issue)}

    _assert_contract_error(PreviewItem, **item_values)
    _assert_contract_error(ValidationIssue, **issue_values)
    _assert_contract_error(PreviewSummary, **summary_values)
    _assert_contract_error(replace, item)
    _assert_contract_error(replace, issue)
    _assert_contract_error(replace, summary)


def test_canonical_output_contains_only_safe_snapshot_types_and_no_upstream_refs():
    exclusion = _excluded()
    resolution = _resolved()
    decision = _diff(resolution)
    validation = _validation()
    summary = _build(
        exclusions=(exclusion,),
        asset_resolutions=(resolution,),
        diff_decisions=(decision,),
        validation_issues=(validation,),
    )
    forbidden_types = (
        ExcludedSourceRef,
        AssetResolution,
        SourceLineage,
        SourceFieldLineage,
        PreviewDiffDecision,
        RedactedValidationIssueInput,
    )
    for value in (*summary.items, *summary.issues, summary):
        assert not isinstance(value, forbidden_types)
        assert all(
            not isinstance(getattr(value, field.name), forbidden_types)
            for field in fields(value)
        )


def test_upstream_lineage_mutation_cannot_change_rendered_output():
    resolution = _resolved()
    summary = _build(
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution),),
    )
    before_json = render_preview_json(summary)
    before_markdown = render_preview_markdown(summary)
    object.__setattr__(resolution.lineage, "sheet_title", NOTE_SENTINEL)
    object.__setattr__(resolution.lineage, "spreadsheet_id", TOKEN_SENTINEL)
    assert render_preview_json(summary) == before_json
    assert render_preview_markdown(summary) == before_markdown
    assert NOTE_SENTINEL not in before_json + before_markdown
    assert TOKEN_SENTINEL not in before_json + before_markdown


def test_json_wire_shape_keys_nulls_and_trailing_newline_are_exact():
    summary = _build(exclusions=(_excluded(metric_id=None),))
    rendered = render_preview_json(summary)
    payload = json.loads(rendered)

    assert list(sorted(payload)) == sorted(
        [
            "schema_version",
            "source_fingerprint",
            "policy_version",
            "normalized_hash",
            "status_counts",
            "severity_counts",
            "items",
            "issues",
        ]
    )
    assert set(payload["status_counts"]) == set(_STATUS_VALUES)
    assert set(payload["severity_counts"]) == set(_SEVERITY_VALUES)
    assert set(payload["items"][0]) == {
        "status",
        "sheet_id",
        "source_row",
        "field",
        "asset_key",
        "metric_id",
        "reasons",
        "candidate_count",
        "rejected_count",
    }
    assert set(payload["issues"][0]) == {
        "severity",
        "reason",
        "sheet_id",
        "source_row",
        "field",
        "asset_key",
        "metric_id",
    }
    assert set(payload["items"][0]["reasons"][0]) == {"domain", "code"}
    assert set(payload["issues"][0]["reason"]) == {"domain", "code"}
    assert payload["items"][0]["metric_id"] is None
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_json_renderer_is_deterministic_and_has_no_object_fallback():
    summary = _build(validation_issues=(_validation(),))
    first = render_preview_json(summary)
    second = render_preview_json(summary)
    assert first == second
    assert "PreviewSummary(" not in first
    assert "object at 0x" not in first


def test_markdown_empty_wire_is_exact():
    summary = _build()
    assert render_preview_markdown(summary) == (
        "# Sync Preview\n"
        "\n"
        "## Metadata\n"
        "\n"
        "- Schema Version: `sync-preview-v1`\n"
        f"- Source Fingerprint: `{HASH_A}`\n"
        "- Policy Version: `synthetic-policy-v1`\n"
        f"- Normalized Hash: `{HASH_B}`\n"
        "\n"
        "## Status Counts\n"
        "\n"
        "| Status | Count |\n"
        "| --- | ---: |\n"
        "| create | 0 |\n"
        "| update | 0 |\n"
        "| archive | 0 |\n"
        "| restore | 0 |\n"
        "| incomplete | 0 |\n"
        "| excluded | 0 |\n"
        "| needs_review | 0 |\n"
        "| unchanged | 0 |\n"
        "\n"
        "## Severity Counts\n"
        "\n"
        "| Severity | Count |\n"
        "| --- | ---: |\n"
        "| blocking_error | 0 |\n"
        "| needs_review | 0 |\n"
        "| excluded | 0 |\n"
        "| warning | 0 |\n"
        "\n"
        "## Items\n"
        "\n"
        "| Status | Sheet ID | Source Row | Field | Identity | Reasons | Candidates | Rejected |\n"
        "| --- | ---: | ---: | --- | --- | --- | ---: | ---: |\n"
        "\n"
        "## Issues\n"
        "\n"
        "| Severity | Sheet ID | Source Row | Field | Identity | Reason |\n"
        "| --- | ---: | ---: | --- | --- | --- |\n"
    )


def test_markdown_item_and_issue_rows_use_exact_safe_wire():
    summary = _build(exclusions=(_excluded(),))
    rendered = render_preview_markdown(summary)
    assert (
        "| excluded | 101 | 7 | public_metric | metric:MET-0001 | "
        "exclusion:oral_only | 0 | 0 |"
    ) in rendered
    assert (
        "| excluded | 101 | 7 | public_metric | metric:MET-0001 | "
        "exclusion:oral_only |"
    ) in rendered
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_typed_asset_identity_wire_is_markdown_safe():
    resolution = _resolved(slot=AssetSourceSlot.NEWS)
    summary = _build(
        asset_resolutions=(resolution,),
        diff_decisions=(_diff(resolution),),
    )
    rendered = render_preview_markdown(summary)
    assert "asset:MREC-0001:news" in rendered
    assert not any(character in str(resolution.asset_key) for character in "|`<>[]()\n\r")


def test_repr_surfaces_are_safe_and_minimal():
    summary = _build(exclusions=(_oral_exclusion(),))
    item = summary.items[0]
    issue = summary.issues[0]
    rendered = repr(item) + repr(issue) + repr(summary)
    assert "status='excluded'" in repr(item)
    assert "severity='excluded'" in repr(issue)
    assert "item_count=1" in repr(summary)
    for forbidden in (
        ORAL_SENTINEL,
        URL_SENTINEL,
        TOKEN_SENTINEL,
        NOTE_SENTINEL,
        HTML_SENTINEL,
        "source_digest",
        "sheet_title",
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize("renderer", (render_preview_json, render_preview_markdown))
@pytest.mark.parametrize("bad", ({}, object(), _context()))
def test_public_renderers_require_exact_canonical_summary(renderer, bad):
    error = _assert_contract_error(renderer, bad)
    assert "object" not in str(error)


def test_invalid_structural_tokens_fail_before_renderer_without_payload_leak():
    for sentinel in (TOKEN_SENTINEL, NOTE_SENTINEL, HTML_SENTINEL):
        error = _assert_contract_error(
            PreviewBuildContext,
            source_fingerprint=HASH_A,
            policy_version=f"bad|{sentinel}",
            normalized_hash=HASH_B,
        )
        assert sentinel not in str(error)
    error = _assert_contract_error(ValidationReasonCode, "BAD|TOKEN_SENTINEL")
    assert "TOKEN_SENTINEL" not in str(error)


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: ValidationReasonCode(
            "VALID_CODE",
            **{CONSTRUCTOR_SENTINEL: "secret"},
        ),
        lambda: PreviewReason(
            PreviewReasonDomain.EXCLUSION,
            "oral_only",
            **{CONSTRUCTOR_SENTINEL: "secret"},
        ),
        lambda: PreviewBuildContext(
            HASH_A,
            "policy-v1",
            HASH_B,
            **{CONSTRUCTOR_SENTINEL: "secret"},
        ),
        lambda: PreviewDiffDecision(
            _key(),
            PreviewStatus.CREATE,
            1,
            1,
            **{CONSTRUCTOR_SENTINEL: "secret"},
        ),
        lambda: RedactedValidationIssueInput(
            ValidationSeverity.WARNING,
            ValidationReasonCode("VALID_CODE"),
            1,
            1,
            PreviewField.PUBLIC_METRIC,
            None,
            None,
            **{CONSTRUCTOR_SENTINEL: "secret"},
        ),
    ),
    ids=(
        "validation-reason-code",
        "preview-reason",
        "preview-build-context",
        "preview-diff-decision",
        "redacted-validation-issue-input",
    ),
)
def test_structural_constructors_reject_unknown_keyword_without_leak(constructor):
    with pytest.raises(PreviewContractError) as exc_info:
        constructor()

    error = exc_info.value
    assert type(error) is PreviewContractError
    assert error.code == STRUCTURAL_CONSTRUCTION_ERROR
    assert CONSTRUCTOR_SENTINEL not in str(error)
    assert CONSTRUCTOR_SENTINEL not in repr(error)


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: PreviewReason(
            PreviewReasonDomain.EXCLUSION,
            "oral_only",
            CONSTRUCTOR_SENTINEL,
        ),
        lambda: PreviewBuildContext(HASH_A),
        lambda: PreviewDiffDecision(
            _key(),
            PreviewStatus.CREATE,
            1,
            1,
            source_row=1,
        ),
    ),
    ids=("too-many-positional", "missing-required", "duplicate-assignment"),
)
def test_structural_constructors_reject_wrong_arity_with_stable_error(constructor):
    with pytest.raises(PreviewContractError) as exc_info:
        constructor()

    error = exc_info.value
    assert type(error) is PreviewContractError
    assert error.code == STRUCTURAL_CONSTRUCTION_ERROR
    assert CONSTRUCTOR_SENTINEL not in str(error)
    assert CONSTRUCTOR_SENTINEL not in repr(error)


def test_structural_construction_guard_preserves_legal_direct_construction():
    values = (
        ValidationReasonCode("VALID_CODE"),
        PreviewReason(PreviewReasonDomain.EXCLUSION, "oral_only"),
        PreviewBuildContext(HASH_A, "policy-v1", HASH_B),
        PreviewDiffDecision(_key(), PreviewStatus.CREATE, 1, 1),
        RedactedValidationIssueInput(
            ValidationSeverity.WARNING,
            ValidationReasonCode("VALID_CODE"),
            1,
            1,
            PreviewField.PUBLIC_METRIC,
            None,
            None,
        ),
    )
    assert tuple(type(value) for value in values) == (
        ValidationReasonCode,
        PreviewReason,
        PreviewBuildContext,
        PreviewDiffDecision,
        RedactedValidationIssueInput,
    )


def test_structural_construction_guard_preserves_specific_validation_codes():
    cases = (
        (
            lambda: ValidationReasonCode("bad"),
            "VALIDATION_REASON_CODE_INVALID",
        ),
        (
            lambda: PreviewReason(
                PreviewReasonDomain.EXCLUSION,
                "incomplete",
            ),
            "PREVIEW_REASON_CODE_INVALID",
        ),
        (
            lambda: PreviewBuildContext(HASH_A, "bad|policy", HASH_B),
            "POLICY_VERSION_INVALID",
        ),
        (
            lambda: PreviewDiffDecision(
                _key(),
                PreviewStatus.INCOMPLETE,
                1,
                1,
            ),
            "DIFF_STATUS_INVALID",
        ),
        (
            lambda: RedactedValidationIssueInput(
                ValidationSeverity.WARNING,
                ValidationReasonCode("VALID_CODE"),
                1,
                1,
                PreviewField.PUBLIC_METRIC,
                _key(),
                MetricId("MET-0001"),
            ),
            "PREVIEW_IDENTITY_CONFLICT",
        ),
    )

    for constructor, expected_code in cases:
        error = _assert_contract_error(constructor)
        assert error.code == expected_code


def test_blocking_issue_does_not_prevent_safe_rendering():
    summary = _build(
        validation_issues=(
            _validation(severity=ValidationSeverity.BLOCKING_ERROR),
        )
    )
    assert dict(summary.severity_counts)[ValidationSeverity.BLOCKING_ERROR] == 1
    assert "blocking_error" in render_preview_json(summary)
    assert "blocking_error" in render_preview_markdown(summary)


def test_module_has_no_wp14_legacy_generic_payload_or_side_effect_surface():
    source = open(sync_preview.__file__, encoding="utf-8").read()
    forbidden_imports = (
        "release_contracts",
        "ReleaseManifest",
        "excel_preview",
        "asset_metadata_preview",
        "asset_apply_preview",
        "review_template",
        "slack_output_preview",
        "sqlite",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "datetime.now",
        "utcnow",
        "uuid",
        "random",
        "subprocess",
        "Path(",
        "open(",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source
    public_field_names = {
        field.name
        for dto in (
            PreviewBuildContext,
            PreviewDiffDecision,
            RedactedValidationIssueInput,
            PreviewReason,
            ValidationIssue,
            PreviewItem,
            PreviewSummary,
        )
        for field in fields(dto)
    }
    assert public_field_names.isdisjoint(
        {
            "payload",
            "details",
            "metadata",
            "context",
            "message",
            "description",
            "text",
            "excerpt",
            "title",
            "url",
            "source_digest",
        }
    )
    assert "ReleaseManifest" not in sync_preview.__dict__


def test_build_and_render_have_zero_runtime_side_effects(monkeypatch):
    resolution = _resolved()
    decision = _diff(resolution)

    def unexpected(*args, **kwargs):
        raise AssertionError("WP15 side effect")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    summary = _build(
        asset_resolutions=(resolution,),
        diff_decisions=(decision,),
    )
    assert render_preview_json(summary)
    assert render_preview_markdown(summary)


def test_schema_and_canonical_field_sets_are_exact():
    summary = _build()
    assert summary.schema_version == "sync-preview-v1"
    assert [field.name for field in fields(PreviewItem)] == [
        "status",
        "sheet_id",
        "source_row",
        "field",
        "asset_key",
        "metric_id",
        "reasons",
        "candidate_count",
        "rejected_count",
    ]
    assert [field.name for field in fields(ValidationIssue)] == [
        "severity",
        "reason",
        "sheet_id",
        "source_row",
        "field",
        "asset_key",
        "metric_id",
    ]
    assert [field.name for field in fields(PreviewSummary)] == [
        "schema_version",
        "source_fingerprint",
        "policy_version",
        "normalized_hash",
        "status_counts",
        "severity_counts",
        "items",
        "issues",
    ]
