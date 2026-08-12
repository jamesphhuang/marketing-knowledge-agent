"""Synthetic WP16 composition proofs, never production orchestration authority."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

import marketing_knowledge_agent.google_normalization as google_normalization
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    BrandId,
    BrandIdentityDecision,
    CanonicalSourceLineage,
    ContentAssetKey,
    ExposureChannel,
    LifecycleStatus,
    MetricId,
    PublishEligibility,
    ReviewStatus,
    SourceRecord,
    SourceRecordId,
    create_public_metric,
)
from marketing_knowledge_agent.canonical_serialization import (
    compute_source_fingerprint,
    serialize_source_snapshot,
)
from marketing_knowledge_agent.capture_policy import (
    ApprovedDomainRule,
    CaptureMode,
    CapturePolicy,
    CapturePolicyDecision,
    CapturePolicyError,
    CaptureRequest,
    DomainClass,
    FetchFailureReason,
    ValidatedCaptureTargetRef,
    classify_fetch_failure,
    evaluate_capture_policy,
)
from marketing_knowledge_agent.captured_chunks import (
    CapturedChunk,
    CapturedChunkError,
    CapturedChunkId,
    SectionAnchor,
    SyntheticChunkSpan,
    build_captured_chunk,
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
    FieldContract,
    FieldValueKind,
    InheritanceReason,
    ResolvedCellValue,
    SourceFieldLineage,
    SourceLineage,
    ValueSource,
    normalize_source_cell,
)
from marketing_knowledge_agent.content_hashing import (
    ApprovedLkgFreshnessPolicy,
    CaptureContentHash,
    CaptureRevisionRef,
    ContentHashingError,
    LkgEligibilityInput,
    LkgEligibilityReason,
    LkgEligibilityResult,
    StaleLkgCandidate,
    compose_stale_lkg,
    compute_capture_content_hash,
    evaluate_lkg_reuse,
)
from marketing_knowledge_agent.google_normalization import (
    ExcludedSourceRef,
    MetricMinimizationError,
    MetricSourceCells,
    PersistenceEligibleMetricInput,
    minimize_public_metric_source,
)
from marketing_knowledge_agent.html_normalization import (
    HTML_NORMALIZER_VERSION,
    HtmlNormalizationResult,
    NormalizationStatus,
    normalize_html,
)
from marketing_knowledge_agent.link_resolution import (
    AssetResolution,
    AssetResolutionStatus,
    AssetSourceSlot,
    EligibleAssetLinkCell,
    LinkExtractionError,
    LinkSource,
    extract_link_candidates,
    resolve_content_asset,
)
from marketing_knowledge_agent.release_contracts import (
    ArtifactRef,
    ArtifactRole,
    CanonicalReleaseInputs,
    ChunkSetHash,
    ReleaseId,
    ReleaseManifest,
    ReleaseManifestHash,
    ReleasePublishState,
    ReleaseContractError,
    build_release_manifest,
    compute_release_manifest_hash,
    serialize_release_manifest,
)
from marketing_knowledge_agent.sheets_contracts import (
    CellData,
    DataValidation,
    DataValidationCondition,
    GoogleValue,
    GridRange,
    SheetSnapshot,
    SheetsReadRequest,
    SheetsReader,
    SpreadsheetSnapshot,
    TextFormatLink,
    TextFormatRun,
)
from marketing_knowledge_agent.sync_preview import (
    PreviewBuildContext,
    PreviewContractError,
    PreviewDiffDecision,
    PreviewField,
    PreviewReasonDomain,
    PreviewStatus,
    PreviewSummary,
    RedactedValidationIssueInput,
    ValidationReasonCode,
    ValidationSeverity,
    build_preview,
    render_preview_json,
    render_preview_markdown,
)
from marketing_knowledge_agent.url_safety import (
    CanonicalURL,
    URLRejectionCode,
    validate_and_canonicalize_url,
)
from sprint0_fixtures import assert_isolated_test_path


UTC = timezone.utc
SHEET_ID = 116
BATCH = "SYNTHETIC-WP16-BATCH"
POLICY_VERSION = "synthetic-wp16-policy-v1"
NORMALIZED_HASH = "sha256:" + "9" * 64
SPREADSHEET_ID_HASH = "sha256:" + "8" * 64
CAPTURED_AT = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
LAST_SUCCESS = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)
LAST_ATTEMPT = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
STALE_ATTEMPT = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)

ORAL_SENTINEL = "ORAL_SENTINEL_WP16_91A7"
TOKEN_SENTINEL = "TOKEN_SENTINEL_WP16_91A7"
RAW_URL_SENTINEL = "RAW_URL_SENTINEL_WP16_91A7"
HTML_SECRET_SENTINEL = "HTML_SECRET_SENTINEL_WP16_91A7"
PUBLIC_BODY_SENTINEL = "PUBLIC_BODY_SENTINEL_WP16_91A7"
SECOND_CHUNK_TEXT = "Secondary synthetic detail"

ASSET_KEY = ContentAssetKey(SourceRecordId("MREC-0016"), AssetType.ARTICLE)
SECOND_ASSET_KEY = ContentAssetKey(SourceRecordId("MREC-0017"), AssetType.ARTICLE)
BRAND_ID = BrandId("BRD-0016")
CAPTURED_CONTENT_ID = CapturedContentId("capture-wp16-primary")
SAFE_URLS = (
    "https://EXAMPLE.test:443/story#rich-fragment",
    "https://example.test/story?utm_source=cell",
    "https://example.test/story?source=formula",
    "https://example.test/story?ref=literal",
)
SOURCE_COLUMNS = {"source_record_id": "A", "article": "H"}
SOURCE_RANGES_BY_ROW = {
    6: {"source_record_id": "A7", "article": "H7"},
    7: {"source_record_id": "A8", "article": "H8"},
}
METRIC_CHANNELS = (
    ExposureChannel.PRESS_RELEASE,
    ExposureChannel.OWNED_MEDIA,
    ExposureChannel.SALESKITS,
    ExposureChannel.VERBAL_BRIEFING,
    ExposureChannel.SPEAKING_DECK,
    ExposureChannel.WEBSITE_RECRUITING,
    ExposureChannel.ADS,
)


class _InMemorySheetsReader:
    def __init__(self, snapshot: SpreadsheetSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[SheetsReadRequest] = []

    def read(self, request: SheetsReadRequest) -> SpreadsheetSnapshot:
        self.requests.append(request)
        return self.snapshot


@dataclass(frozen=True)
class _PreReleaseComposition:
    snapshot: SpreadsheetSnapshot
    source_bytes: bytes
    source_fingerprint: str
    link_cell: ResolvedCellValue
    canonical_lineage: CanonicalSourceLineage
    oral_exclusion: ExcludedSourceRef
    asset_resolution: AssetResolution
    capture_policy_decision: CapturePolicyDecision
    capture_request: CaptureRequest
    normalization: HtmlNormalizationResult
    capture_hash: CaptureContentHash
    captured_content: CapturedContent
    chunks: tuple[CapturedChunk, ...]
    release_inputs: CanonicalReleaseInputs


@dataclass(frozen=True)
class _HappyComposition:
    snapshot: SpreadsheetSnapshot
    source_bytes: bytes
    source_fingerprint: str
    link_cell: ResolvedCellValue
    canonical_lineage: CanonicalSourceLineage
    oral_exclusion: ExcludedSourceRef
    asset_resolution: AssetResolution
    capture_policy_decision: CapturePolicyDecision
    capture_request: CaptureRequest
    normalization: HtmlNormalizationResult
    capture_hash: CaptureContentHash
    captured_content: CapturedContent
    chunks: tuple[CapturedChunk, ...]
    preview: PreviewSummary
    preview_json: str
    preview_markdown: str
    release_inputs: CanonicalReleaseInputs
    manifest: ReleaseManifest
    manifest_bytes: bytes
    manifest_hash: ReleaseManifestHash


@dataclass(frozen=True)
class _StaleComposition:
    eligibility_input: LkgEligibilityInput
    eligibility_result: LkgEligibilityResult
    candidate: StaleLkgCandidate
    captured_content: CapturedContent
    chunks: tuple[CapturedChunk, ...]
    manifest: ReleaseManifest


def _article_cell(
    *,
    article_row: int,
    rich_url: str = SAFE_URLS[0],
    cell_url: str = SAFE_URLS[1],
    formula_url: str = SAFE_URLS[2],
    literal_text: str = SAFE_URLS[3],
    title_only: bool = False,
) -> CellData:
    return CellData(
        row_index=article_row,
        column_index=7,
        formatted_value=("Synthetic Article" if title_only else literal_text),
        effective_value=GoogleValue(
            string_value=("Synthetic Article" if title_only else literal_text)
        ),
        user_entered_value=(
            None
            if title_only
            else GoogleValue(
                formula_value=f'=HYPERLINK("{formula_url}", "Synthetic Article")'
            )
        ),
        hyperlink=None if title_only else cell_url,
        text_format_runs=(
            ()
            if title_only
            else (
                TextFormatRun(
                    start_index=0,
                    link=TextFormatLink(uri=rich_url),
                ),
            )
        ),
    )


def _snapshot(
    *,
    article_row: int = 6,
    rich_url: str = SAFE_URLS[0],
    cell_url: str = SAFE_URLS[1],
    formula_url: str = SAFE_URLS[2],
    literal_text: str = SAFE_URLS[3],
    title_only: bool = False,
    reverse_cells: bool = False,
    additional_article_cells: tuple[CellData, ...] = (),
) -> SpreadsheetSnapshot:
    article_cell = _article_cell(
        article_row=article_row,
        rich_url=rich_url,
        cell_url=cell_url,
        formula_url=formula_url,
        literal_text=literal_text,
        title_only=title_only,
    )
    cells = (
        article_cell,
        *additional_article_cells,
        CellData(
            row_index=2,
            column_index=1,
            formatted_value="Synthetic merged metric",
            effective_value=GoogleValue(string_value="Synthetic merged metric"),
        ),
        CellData(row_index=2, column_index=2),
        CellData(
            row_index=3,
            column_index=3,
            formatted_value="42",
            effective_value=GoogleValue(number_value=42),
            user_entered_value=GoogleValue(formula_value="=SUM(40,2)"),
        ),
    )
    if reverse_cells:
        cells = tuple(reversed(cells))
    return SpreadsheetSnapshot(
        spreadsheet_id="synthetic-spreadsheet-wp16",
        sheets=(
            SheetSnapshot(
                sheet_id=SHEET_ID,
                title="Synthetic WP16 Sources",
                row_count=20,
                column_count=12,
                cells=cells,
                merges=(
                    GridRange(
                        sheet_id=SHEET_ID,
                        start_row_index=2,
                        end_row_index=3,
                        start_column_index=1,
                        end_column_index=3,
                    ),
                ),
            ),
        ),
    )


def _read_snapshot(snapshot: SpreadsheetSnapshot) -> SpreadsheetSnapshot:
    reader = _InMemorySheetsReader(snapshot)
    request = SheetsReadRequest(
        spreadsheet_id=snapshot.spreadsheet_id,
        ranges=("'Synthetic WP16 Sources'!A1:L20",),
        fields=("sheets.properties", "sheets.data.rowData.values", "sheets.merges"),
    )

    assert isinstance(reader, SheetsReader)
    assert not hasattr(reader, "write")
    assert reader.read(request) is snapshot
    assert reader.requests == [request]
    return snapshot


def _canonical_lineage(
    resolved: ResolvedCellValue,
    *,
    source_ranges: dict[str, str],
) -> CanonicalSourceLineage:
    lineage = resolved.lineage
    # WP5 fixes the 0-based WP3 row to 1-based canonical-row relationship.
    return CanonicalSourceLineage(
        spreadsheet_id_hash=SPREADSHEET_ID_HASH,
        sheet_id=lineage.sheet_id,
        sheet_title=lineage.sheet_title,
        source_row=lineage.source_row_index + 1,
        source_columns=SOURCE_COLUMNS,
        source_ranges=source_ranges,
        source_fingerprint=lineage.source_fingerprint,
        sync_batch_id=lineage.sync_batch_id,
    )


def _metric_channel_cell(
    lineage: CanonicalSourceLineage,
    channel: ExposureChannel,
    value: bool,
) -> ResolvedCellValue:
    row_index = lineage.source_row - 1
    column_index = METRIC_CHANNELS.index(channel) + 6
    source_cell = CellData(
        row_index=row_index,
        column_index=column_index,
        formatted_value=str(value),
        effective_value=GoogleValue(bool_value=value),
        data_validation=DataValidation(
            condition=DataValidationCondition(condition_type="BOOLEAN")
        ),
    )
    return ResolvedCellValue(
        normalized_value=value,
        display_value=str(value),
        value_source=ValueSource.EFFECTIVE_VALUE,
        source_was_formula=False,
        source_cell=source_cell,
        value_cell=source_cell,
        field_contract=FieldContract(
            field_name=channel.value,
            value_kind=FieldValueKind.BOOLEAN,
            source_column_index=column_index,
        ),
        lineage=SourceLineage(
            spreadsheet_id="synthetic-spreadsheet-wp16",
            sheet_id=lineage.sheet_id,
            sheet_title=lineage.sheet_title,
            sheet_hidden=False,
            source_row_index=row_index,
            source_column_index=column_index,
            source_fingerprint=lineage.source_fingerprint,
            sync_batch_id=lineage.sync_batch_id,
        ),
        field_lineage=SourceFieldLineage(
            field_name=channel.value,
            target_row_index=row_index,
            target_column_index=column_index,
            value_row_index=row_index,
            value_column_index=column_index,
            merge_anchor_row_index=None,
            merge_anchor_column_index=None,
            merge_range=None,
            inherited_from_merge=False,
            inheritance_reason=InheritanceReason.LOCAL,
        ),
    )


def _written_metric_source(
    lineage: CanonicalSourceLineage,
    evidence_urls: tuple[str, ...],
) -> MetricSourceCells:
    channel_values = {
        channel: channel is ExposureChannel.PRESS_RELEASE
        for channel in METRIC_CHANNELS
    }
    return MetricSourceCells(
        metric_id=MetricId("MET-0016"),
        metric_type="Synthetic metric",
        indicator="Synthetic indicator",
        approved_statement="Synthetic approved statement",
        note=None,
        maintenance_updated_at=date(2026, 8, 11),
        evidence_urls=evidence_urls,
        channel_cells=tuple(
            _metric_channel_cell(lineage, channel, channel_values[channel])
            for channel in METRIC_CHANNELS
        ),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        can_quote_externally=True,
        source_lineage=lineage,
    )


def _oral_exclusion(lineage: CanonicalSourceLineage) -> ExcludedSourceRef:
    transient_sensitive_input = MetricSourceCells(
        metric_id=MetricId("MET-0016"),
        metric_type="Synthetic metric",
        indicator="Synthetic indicator",
        approved_statement=ORAL_SENTINEL,
        note="不留文字紀錄",
        maintenance_updated_at=date(2026, 8, 11),
        evidence_urls=(),
        channel_cells=(),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        can_quote_externally=False,
        source_lineage=lineage,
    )
    result = minimize_public_metric_source(transient_sensitive_input)
    assert type(result) is ExcludedSourceRef
    return result


def _capture_target_from_resolved_asset(
    resolution: AssetResolution,
    *,
    domain_class: DomainClass,
) -> ValidatedCaptureTargetRef:
    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert resolution.asset_key == ASSET_KEY
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].canonical_url.value == "https://example.test/story"
    assert domain_class is DomainClass.APPROVED_THIRD_PARTY
    return ValidatedCaptureTargetRef("synthetic-wp16-validated-target")


def _project_sections(result: HtmlNormalizationResult) -> tuple[Section, ...]:
    return tuple(
        Section(heading=section.heading, text=section.text)
        for section in result.sections
    )


def _source_record(lineage: CanonicalSourceLineage) -> SourceRecord:
    return SourceRecord(
        source_record_id=ASSET_KEY.source_record_id,
        brand_identity=BrandIdentityDecision(
            review_status=ReviewStatus.APPROVED,
            brand_id=BRAND_ID,
        ),
        interview_year=2026,
        source_name="Synthetic WP16 source record",
        sales_category_lv1=None,
        sales_category_lv2=None,
        tags=(),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        source_lineage=lineage,
    )


def _artifacts() -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            ArtifactRole.OBSIDIAN_TREE,
            "wp16/obsidian_tree",
            "sha256:" + "2" * 64,
        ),
        ArtifactRef(
            ArtifactRole.OFFICIAL_SQLITE,
            "wp16/official.sqlite",
            "sha256:" + "3" * 64,
        ),
        ArtifactRef(
            ArtifactRole.OFFICIAL_VECTOR,
            "wp16/official.vector",
            "sha256:" + "4" * 64,
        ),
    )


def _release_inputs(
    *,
    source_fingerprint: str,
    captured_content: CapturedContent,
    chunks: tuple[CapturedChunk, ...],
    capture_policy_decision: CapturePolicyDecision,
    source_row_count: int = 1,
    stale_proofs: tuple[
        tuple[CapturedContentId, LkgEligibilityInput, LkgEligibilityResult], ...
    ] = (),
) -> CanonicalReleaseInputs:
    return CanonicalReleaseInputs(
        release_id=ReleaseId("release-wp16-candidate"),
        metadata_sync_batch_id=BATCH,
        source_fingerprint=source_fingerprint,
        source_row_counts=((SHEET_ID, source_row_count),),
        entity_counts=(
            ("brand", 1),
            ("captured_content", 1),
            ("chunk", len(chunks)),
        ),
        excluded_counts=(("oral_only", 1),),
        captured_contents=(captured_content,),
        capture_policy_decisions=(
            (captured_content.captured_content_id, capture_policy_decision),
        ),
        stale_proofs=stale_proofs,
        captured_chunks=chunks,
        artifacts=_artifacts(),
        validator_versions=(
            ("url_safety", "wp7-v1-2026-08-09"),
            ("capture_policy", POLICY_VERSION),
            ("html_normalizer", HTML_NORMALIZER_VERSION),
        ),
        previous_release=None,
        created_at=CREATED_AT,
    )


def _compose_complete_candidate(
    *,
    asset_resolutions: tuple[AssetResolution, ...],
    preview: PreviewSummary,
    expected_current_asset_keys: tuple[ContentAssetKey, ...],
    captured_contents: tuple[CapturedContent, ...],
    capture_policy_associations: tuple[
        tuple[CapturedContentId, CapturePolicyDecision], ...
    ],
    release_inputs: CanonicalReleaseInputs,
    release_builder: Callable[[CanonicalReleaseInputs], ReleaseManifest] = (
        build_release_manifest
    ),
) -> ReleaseManifest | None:
    # WP14 validates only caller-supplied composition. This test helper expresses
    # the frozen caller obligation and never submits a partial batch to WP14.
    if any(
        issue.severity
        in (ValidationSeverity.BLOCKING_ERROR, ValidationSeverity.NEEDS_REVIEW)
        for issue in preview.issues
    ):
        return None
    if any(
        resolution.status is not AssetResolutionStatus.RESOLVED_CANDIDATE
        for resolution in asset_resolutions
    ):
        return None
    expected = set(expected_current_asset_keys)
    if len(expected) != len(expected_current_asset_keys):
        return None

    resolutions_by_key = {}
    for resolution in asset_resolutions:
        if resolution.asset_key in resolutions_by_key:
            return None
        resolutions_by_key[resolution.asset_key] = resolution

    primary_captured_by_key = {}
    for item in captured_contents:
        if (
            item.authority_role is AuthorityRole.PRIMARY_CONTENT
            and item.asset_key is not None
        ):
            if item.asset_key in primary_captured_by_key:
                return None
            primary_captured_by_key[item.asset_key] = item

    resolved_lifecycle_statuses = {
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    }
    preview_items_by_key = {}
    for item in preview.items:
        if item.status in resolved_lifecycle_statuses:
            if item.asset_key is None or item.asset_key in preview_items_by_key:
                return None
            preview_items_by_key[item.asset_key] = item

    if (
        expected != set(resolutions_by_key)
        or expected != set(primary_captured_by_key)
        or expected != set(preview_items_by_key)
    ):
        return None

    for asset_key in expected:
        resolution = resolutions_by_key[asset_key]
        captured = primary_captured_by_key[asset_key]
        preview_item = preview_items_by_key[asset_key]
        if len(resolution.candidates) != 1:
            return None
        if resolution.candidates[0].canonical_url != captured.canonical_url:
            return None
        sheet_id = resolution.lineage.sheet_id
        source_row = resolution.lineage.source_row_index + 1
        if (
            captured.authority_role is not AuthorityRole.PRIMARY_CONTENT
            or captured.source_lineage.sheet_id != sheet_id
            or captured.source_lineage.source_row != source_row
            or preview_item.sheet_id != sheet_id
            or preview_item.source_row != source_row
        ):
            return None
        if (
            preview_item.candidate_count != len(resolution.candidates)
            or preview_item.rejected_count
            != len(resolution.rejected_occurrences)
        ):
            return None
        expected_rejection_projection = set()
        for occurrence in resolution.rejected_occurrences:
            rejection_code = occurrence.rejection_code
            if type(rejection_code) is not URLRejectionCode:
                return None
            expected_rejection_projection.add(
                (rejection_code.value, ValidationSeverity.WARNING)
            )
        preview_rejection_projection = {
            (issue.reason.code, issue.severity)
            for issue in preview.issues
            if issue.asset_key == asset_key
            and issue.reason.domain is PreviewReasonDomain.URL_REJECTION
        }
        if preview_rejection_projection != expected_rejection_projection:
            return None

    source_fingerprint = preview.source_fingerprint
    if release_inputs.source_fingerprint != source_fingerprint:
        return None
    if any(
        resolution.lineage.source_fingerprint != source_fingerprint
        for resolution in asset_resolutions
    ) or any(
        item.source_lineage.source_fingerprint != source_fingerprint
        for item in captured_contents
    ):
        return None

    metadata_batch = release_inputs.metadata_sync_batch_id
    if any(
        resolution.lineage.sync_batch_id != metadata_batch
        for resolution in asset_resolutions
    ) or any(
        item.sync_batch_id != metadata_batch
        or item.source_lineage.sync_batch_id != metadata_batch
        for item in captured_contents
    ):
        return None

    captured_by_id = {
        item.captured_content_id: item for item in captured_contents
    }
    submitted_captured_by_id = {
        item.captured_content_id: item for item in release_inputs.captured_contents
    }
    if (
        len(captured_by_id) != len(captured_contents)
        or len(submitted_captured_by_id) != len(release_inputs.captured_contents)
        or captured_by_id != submitted_captured_by_id
    ):
        return None

    policy_by_id = dict(capture_policy_associations)
    submitted_policy_by_id = dict(release_inputs.capture_policy_decisions)
    if (
        len(policy_by_id) != len(capture_policy_associations)
        or len(submitted_policy_by_id)
        != len(release_inputs.capture_policy_decisions)
        or policy_by_id != submitted_policy_by_id
        or set(policy_by_id) != set(captured_by_id)
    ):
        return None
    # This Sprint 0 synthetic preview and capture share one policy authority.
    submitted_policy_versions = {
        decision.policy_version for decision in submitted_policy_by_id.values()
    }
    if (
        len(submitted_policy_versions) != 1
        or preview.policy_version not in submitted_policy_versions
    ):
        return None
    if any(
        decision.mode is not CaptureMode.FULL_TEXT
        for decision in policy_by_id.values()
    ):
        return None
    return release_builder(release_inputs)


def _assemble_pre_release(
    *,
    snapshot: SpreadsheetSnapshot,
    article_row: int = 6,
    source_row_count: int = 1,
) -> _PreReleaseComposition:
    """Assemble the fixed synthetic capture witness without submitting WP14."""
    snapshot = _read_snapshot(snapshot)
    source_bytes = serialize_source_snapshot(snapshot)
    source_fingerprint = compute_source_fingerprint(snapshot)
    link_cell = normalize_source_cell(
        snapshot,
        sheet_id=SHEET_ID,
        source_row_index=article_row,
        field_contract=FieldContract(
            field_name="article_asset",
            value_kind=FieldValueKind.TEXT,
            source_column_index=7,
        ),
        source_fingerprint=source_fingerprint,
        sync_batch_id=BATCH,
    )
    canonical_lineage = _canonical_lineage(
        link_cell,
        source_ranges=SOURCE_RANGES_BY_ROW[article_row],
    )
    oral_exclusion = _oral_exclusion(canonical_lineage)

    candidates = extract_link_candidates(
        EligibleAssetLinkCell(link_cell, AssetSourceSlot.ARTICLE)
    )
    validation_results = tuple(
        validate_and_canonicalize_url(candidate) for candidate in candidates
    )
    asset_resolution = resolve_content_asset(
        asset_key=ASSET_KEY,
        brand_id=BRAND_ID,
        normalized_title="Synthetic Article",
        lineage=link_cell.lineage,
        field_lineage=link_cell.field_lineage,
        candidates=candidates,
        validation_results=validation_results,
    )
    assert asset_resolution is not None

    capture_policy_decision = evaluate_capture_policy(
        CapturePolicy(
            policy_version=POLICY_VERSION,
            approved_domain_rules=(
                ApprovedDomainRule("example.test", CaptureMode.FULL_TEXT),
            ),
        ),
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key="example.test",
    )
    capture_request = CaptureRequest(
        _capture_target_from_resolved_asset(
            asset_resolution,
            domain_class=DomainClass.APPROVED_THIRD_PARTY,
        ),
        capture_policy_decision,
    )

    normalization = normalize_html(
        "<html><body>"
        f"<nav>{HTML_SECRET_SENTINEL}</nav>"
        f"<script>{HTML_SECRET_SENTINEL}</script>"
        "<h1>Synthetic Article</h1>"
        f"<p>{PUBLIC_BODY_SENTINEL}</p>"
        f"<ul><li>{SECOND_CHUNK_TEXT}</li></ul>"
        "</body></html>",
        expected_parser_version=HTML_NORMALIZER_VERSION,
    )
    assert normalization.status is NormalizationStatus.SUCCESS
    capture_hash = compute_capture_content_hash(normalization)
    assert normalization.clean_body is not None
    canonical_url = asset_resolution.candidates[0].canonical_url
    captured_content = CapturedContent(
        captured_content_id=CAPTURED_CONTENT_ID,
        asset_key=ASSET_KEY,
        metric_id=None,
        evidence_relationship_id=None,
        authority_role=AuthorityRole.PRIMARY_CONTENT,
        source_url=canonical_url,
        canonical_url=canonical_url,
        source_domain="example.test",
        content_type="text/html",
        title=normalization.title,
        clean_body=normalization.clean_body,
        section_structure=_project_sections(normalization),
        capture_status=CaptureStatus.SUCCESS,
        captured_at=CAPTURED_AT,
        last_successful_capture_at=LAST_SUCCESS,
        last_capture_attempt_at=LAST_ATTEMPT,
        content_hash=str(capture_hash),
        parser_version=normalization.parser_version,
        source_http_metadata=SafeHttpMetadata(status_code=200),
        previous_content_hash=None,
        searchable=True,
        source_lineage=canonical_lineage,
        sync_batch_id=BATCH,
    )
    revision = CaptureRevisionRef(
        captured_content_id=captured_content.captured_content_id,
        content_hash=capture_hash,
        parser_version=captured_content.parser_version,
    )
    source_record = _source_record(canonical_lineage)
    chunks = tuple(
        build_captured_chunk(
            captured_content=captured_content,
            revision_ref=revision,
            span=SyntheticChunkSpan(
                text=text,
                start=normalization.clean_body.index(text),
                end=normalization.clean_body.index(text) + len(text),
                section_anchor=SectionAnchor(anchor),
                section_heading="Synthetic Article",
                ordinal=ordinal,
            ),
            primary_source_record=source_record,
        )
        for ordinal, (text, anchor) in enumerate(
            (
                (PUBLIC_BODY_SENTINEL, "public-body"),
                (SECOND_CHUNK_TEXT, "secondary-detail"),
            )
        )
    )

    release_inputs = _release_inputs(
        source_fingerprint=source_fingerprint,
        captured_content=captured_content,
        chunks=chunks,
        capture_policy_decision=capture_policy_decision,
        source_row_count=source_row_count,
    )
    return _PreReleaseComposition(
        snapshot=snapshot,
        source_bytes=source_bytes,
        source_fingerprint=source_fingerprint,
        link_cell=link_cell,
        canonical_lineage=canonical_lineage,
        oral_exclusion=oral_exclusion,
        asset_resolution=asset_resolution,
        capture_policy_decision=capture_policy_decision,
        capture_request=capture_request,
        normalization=normalization,
        capture_hash=capture_hash,
        captured_content=captured_content,
        chunks=chunks,
        release_inputs=release_inputs,
    )


def _assemble_happy(
    *,
    article_row: int = 6,
    reverse_cells: bool = False,
) -> _HappyComposition:
    """Assemble one fixed synthetic contract path using only public WP APIs."""
    pre_release = _assemble_pre_release(
        snapshot=_snapshot(article_row=article_row, reverse_cells=reverse_cells),
        article_row=article_row,
    )

    # NORMALIZED_HASH is an approved synthetic transport reference only; WP16
    # does not claim or imitate a production normalized-hash producer.
    preview = build_preview(
        PreviewBuildContext(
            source_fingerprint=pre_release.source_fingerprint,
            policy_version=POLICY_VERSION,
            normalized_hash=NORMALIZED_HASH,
        ),
        (pre_release.oral_exclusion,),
        (pre_release.asset_resolution,),
        (
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=article_row + 1,
            ),
        ),
        (),
    )
    preview_json = render_preview_json(preview)
    preview_markdown = render_preview_markdown(preview)
    manifest = _compose_complete_candidate(
        asset_resolutions=(pre_release.asset_resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(pre_release.captured_content,),
        capture_policy_associations=(
            (
                pre_release.captured_content.captured_content_id,
                pre_release.capture_policy_decision,
            ),
        ),
        release_inputs=pre_release.release_inputs,
    )
    assert manifest is not None
    manifest_bytes = serialize_release_manifest(manifest)
    return _HappyComposition(
        snapshot=pre_release.snapshot,
        source_bytes=pre_release.source_bytes,
        source_fingerprint=pre_release.source_fingerprint,
        link_cell=pre_release.link_cell,
        canonical_lineage=pre_release.canonical_lineage,
        oral_exclusion=pre_release.oral_exclusion,
        asset_resolution=pre_release.asset_resolution,
        capture_policy_decision=pre_release.capture_policy_decision,
        capture_request=pre_release.capture_request,
        normalization=pre_release.normalization,
        capture_hash=pre_release.capture_hash,
        captured_content=pre_release.captured_content,
        chunks=pre_release.chunks,
        preview=preview,
        preview_json=preview_json,
        preview_markdown=preview_markdown,
        release_inputs=pre_release.release_inputs,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_hash=compute_release_manifest_hash(manifest),
    )


def _resolve_snapshot(
    snapshot: SpreadsheetSnapshot,
    *,
    article_row: int = 6,
    asset_key: ContentAssetKey = ASSET_KEY,
) -> tuple[str, ResolvedCellValue, AssetResolution]:
    source_fingerprint = compute_source_fingerprint(snapshot)
    resolved = normalize_source_cell(
        snapshot,
        sheet_id=SHEET_ID,
        source_row_index=article_row,
        field_contract=FieldContract(
            field_name="article_asset",
            value_kind=FieldValueKind.TEXT,
            source_column_index=7,
        ),
        source_fingerprint=source_fingerprint,
        sync_batch_id=BATCH,
    )
    candidates = extract_link_candidates(
        EligibleAssetLinkCell(resolved, AssetSourceSlot.ARTICLE)
    )
    resolution = resolve_content_asset(
        asset_key=asset_key,
        brand_id=BRAND_ID,
        normalized_title="Synthetic Article",
        lineage=resolved.lineage,
        field_lineage=resolved.field_lineage,
        candidates=candidates,
        validation_results=tuple(
            validate_and_canonicalize_url(candidate) for candidate in candidates
        ),
    )
    assert resolution is not None
    return source_fingerprint, resolved, resolution


def _preview_for(
    *,
    source_fingerprint: str,
    resolutions: tuple[AssetResolution, ...],
    diff_decisions: tuple[PreviewDiffDecision, ...],
    validation_issues: tuple[RedactedValidationIssueInput, ...] = (),
) -> PreviewSummary:
    return build_preview(
        PreviewBuildContext(
            source_fingerprint=source_fingerprint,
            policy_version=POLICY_VERSION,
            normalized_hash=NORMALIZED_HASH,
        ),
        (),
        resolutions,
        diff_decisions,
        validation_issues,
    )


def _assert_not_submitted_to_wp14(
    *,
    resolutions: tuple[AssetResolution, ...],
    preview: PreviewSummary,
    expected_keys: tuple[ContentAssetKey, ...],
    captured_contents: tuple[CapturedContent, ...],
    policy_associations: tuple[
        tuple[CapturedContentId, CapturePolicyDecision], ...
    ],
    release_inputs: CanonicalReleaseInputs,
) -> None:
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    manifest = _compose_complete_candidate(
        asset_resolutions=resolutions,
        preview=preview,
        expected_current_asset_keys=expected_keys,
        captured_contents=captured_contents,
        capture_policy_associations=policy_associations,
        release_inputs=release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def _stale_captured_content(
    previous: CapturedContent,
    candidate: StaleLkgCandidate | None,
    *,
    last_attempt: datetime,
) -> CapturedContent:
    return CapturedContent(
        captured_content_id=previous.captured_content_id,
        asset_key=previous.asset_key,
        metric_id=previous.metric_id,
        evidence_relationship_id=previous.evidence_relationship_id,
        authority_role=previous.authority_role,
        source_url=previous.source_url,
        canonical_url=previous.canonical_url,
        source_domain=previous.source_domain,
        content_type=previous.content_type,
        title=previous.title,
        clean_body=previous.clean_body,
        section_structure=previous.section_structure,
        capture_status=CaptureStatus.STALE,
        captured_at=(candidate.captured_at if candidate else previous.captured_at),
        last_successful_capture_at=(
            candidate.last_successful_capture_at
            if candidate
            else previous.last_successful_capture_at
        ),
        last_capture_attempt_at=last_attempt,
        content_hash=previous.content_hash,
        parser_version=previous.parser_version,
        source_http_metadata=previous.source_http_metadata,
        previous_content_hash=(
            candidate.previous_content_hash
            if candidate
            else previous.previous_content_hash
        ),
        searchable=candidate.searchable if candidate else previous.searchable,
        source_lineage=previous.source_lineage,
        sync_batch_id=previous.sync_batch_id,
    )


def _captured_content_with_canonical_url(
    previous: CapturedContent,
    canonical_url: CanonicalURL,
) -> CapturedContent:
    return CapturedContent(
        captured_content_id=previous.captured_content_id,
        asset_key=previous.asset_key,
        metric_id=previous.metric_id,
        evidence_relationship_id=previous.evidence_relationship_id,
        authority_role=previous.authority_role,
        source_url=previous.source_url,
        canonical_url=canonical_url,
        source_domain=previous.source_domain,
        content_type=previous.content_type,
        title=previous.title,
        clean_body=previous.clean_body,
        section_structure=previous.section_structure,
        capture_status=previous.capture_status,
        captured_at=previous.captured_at,
        last_successful_capture_at=previous.last_successful_capture_at,
        last_capture_attempt_at=previous.last_capture_attempt_at,
        content_hash=previous.content_hash,
        parser_version=previous.parser_version,
        source_http_metadata=previous.source_http_metadata,
        previous_content_hash=previous.previous_content_hash,
        searchable=previous.searchable,
        source_lineage=previous.source_lineage,
        sync_batch_id=previous.sync_batch_id,
    )


def _build_chunks(
    captured_content: CapturedContent,
    *,
    stale_input: LkgEligibilityInput | None = None,
    stale_result: LkgEligibilityResult | None = None,
) -> tuple[CapturedChunk, ...]:
    assert captured_content.clean_body is not None
    assert captured_content.content_hash is not None
    assert captured_content.parser_version is not None
    revision = CaptureRevisionRef(
        captured_content_id=captured_content.captured_content_id,
        content_hash=CaptureContentHash(captured_content.content_hash),
        parser_version=captured_content.parser_version,
    )
    source_record = _source_record(captured_content.source_lineage)
    return tuple(
        build_captured_chunk(
            captured_content=captured_content,
            revision_ref=revision,
            span=SyntheticChunkSpan(
                text=text,
                start=captured_content.clean_body.index(text),
                end=captured_content.clean_body.index(text) + len(text),
                section_anchor=SectionAnchor(anchor),
                section_heading="Synthetic Article",
                ordinal=ordinal,
            ),
            primary_source_record=source_record,
            stale_lkg_input=stale_input,
            stale_lkg_result=stale_result,
        )
        for ordinal, (text, anchor) in enumerate(
            (
                (PUBLIC_BODY_SENTINEL, "public-body"),
                (SECOND_CHUNK_TEXT, "secondary-detail"),
            )
        )
    )


def _compose_stale(composition: _HappyComposition) -> _StaleComposition:
    eligibility_input = LkgEligibilityInput(
        current_canonical_url=composition.captured_content.canonical_url,
        previous_success=composition.captured_content,
        current_capture_policy=composition.capture_policy_decision,
        current_failure_category=classify_fetch_failure(FetchFailureReason.TIMEOUT),
        governance_allowed=True,
        identity_reconciled=True,
        freshness_policy=ApprovedLkgFreshnessPolicy(
            policy_version="synthetic-wp16-freshness-v1",
            max_age=timedelta(days=7),
        ),
        current_attempt_at=STALE_ATTEMPT,
    )
    eligibility_result = evaluate_lkg_reuse(eligibility_input)
    candidate = compose_stale_lkg(eligibility_input, eligibility_result)
    stale = _stale_captured_content(
        composition.captured_content,
        candidate,
        last_attempt=STALE_ATTEMPT,
    )
    chunks = _build_chunks(
        stale,
        stale_input=eligibility_input,
        stale_result=eligibility_result,
    )
    inputs = _release_inputs(
        source_fingerprint=composition.source_fingerprint,
        captured_content=stale,
        chunks=chunks,
        capture_policy_decision=composition.capture_policy_decision,
        stale_proofs=(
            (stale.captured_content_id, eligibility_input, eligibility_result),
        ),
    )
    manifest = _compose_complete_candidate(
        asset_resolutions=(composition.asset_resolution,),
        preview=composition.preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(stale,),
        capture_policy_associations=(
            (stale.captured_content_id, composition.capture_policy_decision),
        ),
        release_inputs=inputs,
    )
    assert manifest is not None
    return _StaleComposition(
        eligibility_input=eligibility_input,
        eligibility_result=eligibility_result,
        candidate=candidate,
        captured_content=stale,
        chunks=chunks,
        manifest=manifest,
    )


def _assert_no_forbidden_sentinels(*outputs: object) -> None:
    forbidden = (
        ORAL_SENTINEL,
        TOKEN_SENTINEL,
        RAW_URL_SENTINEL,
        HTML_SECRET_SENTINEL,
    )
    for output in outputs:
        rendered = (
            output.decode("utf-8")
            if type(output) is bytes
            else output
            if type(output) is str
            else repr(output)
        )
        for sentinel in forbidden:
            assert sentinel not in rendered


def test_happy_complete_candidate_composes_wp0_through_wp15(tmp_path: Path):
    composition = _assemble_happy()

    assert composition.source_fingerprint.startswith("sha256:")
    assert composition.source_fingerprint != NORMALIZED_HASH
    assert composition.link_cell.source_was_formula is True
    assert [
        candidate.source
        for candidate in extract_link_candidates(
            EligibleAssetLinkCell(composition.link_cell, AssetSourceSlot.ARTICLE)
        )
    ] == [
        LinkSource.RICH_TEXT,
        LinkSource.CELL_HYPERLINK,
        LinkSource.HYPERLINK_FORMULA,
        LinkSource.LITERAL_TEXT,
    ]
    assert composition.asset_resolution.status is (
        AssetResolutionStatus.RESOLVED_CANDIDATE
    )
    assert len(composition.asset_resolution.candidates) == 1
    assert len(
        composition.asset_resolution.candidates[0].provenance_occurrences
    ) == 4
    assert composition.capture_policy_decision.mode is CaptureMode.FULL_TEXT
    assert composition.capture_request.policy_version == POLICY_VERSION
    assert composition.normalization.status is NormalizationStatus.SUCCESS
    assert HTML_SECRET_SENTINEL not in composition.normalization.clean_body
    assert PUBLIC_BODY_SENTINEL in composition.normalization.clean_body
    assert composition.captured_content.capture_status is CaptureStatus.SUCCESS
    assert composition.captured_content.searchable is True
    assert len(composition.chunks) == 2
    assert all(type(chunk.metadata.chunk_id) is CapturedChunkId for chunk in composition.chunks)
    assert composition.manifest.publish_state is ReleasePublishState.CANDIDATE
    assert type(composition.manifest.captured_revisions[0].chunk_set_hash) is ChunkSetHash
    assert type(composition.manifest_hash) is ReleaseManifestHash
    assert dict(composition.preview.status_counts)[PreviewStatus.CREATE] == 1

    manifest_path = assert_isolated_test_path(tmp_path / "manifest.json", tmp_path)
    preview_json_path = assert_isolated_test_path(tmp_path / "preview.json", tmp_path)
    preview_markdown_path = assert_isolated_test_path(tmp_path / "preview.md", tmp_path)
    manifest_path.write_bytes(composition.manifest_bytes)
    preview_json_path.write_text(composition.preview_json, encoding="utf-8")
    preview_markdown_path.write_text(composition.preview_markdown, encoding="utf-8")
    assert manifest_path.read_bytes() == composition.manifest_bytes
    assert preview_json_path.read_text(encoding="utf-8") == composition.preview_json
    assert preview_markdown_path.read_text(encoding="utf-8") == composition.preview_markdown
    _assert_no_forbidden_sentinels(
        manifest_path.read_bytes(),
        preview_json_path.read_text(encoding="utf-8"),
        preview_markdown_path.read_text(encoding="utf-8"),
    )


def test_wp2_and_wp3_preserve_formula_merge_and_collection_reorder_contracts():
    snapshot = _snapshot()
    fingerprint = compute_source_fingerprint(snapshot)
    merged = normalize_source_cell(
        snapshot,
        sheet_id=SHEET_ID,
        source_row_index=2,
        field_contract=FieldContract(
            field_name="merged_metric",
            value_kind=FieldValueKind.TEXT,
            source_column_index=2,
            merge_inheritance_allowed=True,
        ),
        source_fingerprint=fingerprint,
        sync_batch_id=BATCH,
    )
    formula = normalize_source_cell(
        snapshot,
        sheet_id=SHEET_ID,
        source_row_index=3,
        field_contract=FieldContract(
            field_name="formula_metric",
            value_kind=FieldValueKind.NUMBER,
            source_column_index=3,
        ),
        source_fingerprint=fingerprint,
        sync_batch_id=BATCH,
    )

    assert merged.normalized_value == "Synthetic merged metric"
    assert merged.field_lineage.inherited_from_merge is True
    assert merged.value_cell.row_index == 2
    assert merged.value_cell.column_index == 1
    assert formula.normalized_value == 42
    assert formula.source_was_formula is True
    assert formula.normalized_value != "=SUM(40,2)"

    reordered = _snapshot(reverse_cells=True)
    assert serialize_source_snapshot(snapshot) == serialize_source_snapshot(reordered)
    assert compute_source_fingerprint(snapshot) == compute_source_fingerprint(reordered)


def test_oral_exclusion_is_irreversible_across_asset_capture_chunk_and_release():
    composition = _assemble_happy()
    exclusion = composition.oral_exclusion

    assert not isinstance(exclusion, PersistenceEligibleMetricInput)
    assert not hasattr(exclusion, "canonical_url")
    assert not hasattr(exclusion, "asset_key")
    with pytest.raises(LinkExtractionError, match="RESOLVED_CELL_VALUE_REQUIRED"):
        EligibleAssetLinkCell(exclusion, AssetSourceSlot.ARTICLE)
    with pytest.raises(
        CapturePolicyError,
        match="VALIDATED_CAPTURE_TARGET_REF_REQUIRED",
    ):
        CaptureRequest(exclusion, composition.capture_policy_decision)

    parent = composition.captured_content
    assert parent.content_hash is not None
    assert parent.parser_version is not None
    with pytest.raises(CapturedChunkError):
        build_captured_chunk(
            captured_content=exclusion,
            revision_ref=CaptureRevisionRef(
                captured_content_id=parent.captured_content_id,
                content_hash=CaptureContentHash(parent.content_hash),
                parser_version=parent.parser_version,
            ),
            span=SyntheticChunkSpan(
                text=PUBLIC_BODY_SENTINEL,
                start=parent.clean_body.index(PUBLIC_BODY_SENTINEL),
                end=(
                    parent.clean_body.index(PUBLIC_BODY_SENTINEL)
                    + len(PUBLIC_BODY_SENTINEL)
                ),
                section_anchor=SectionAnchor("oral-boundary-probe"),
                section_heading="Synthetic Article",
                ordinal=0,
            ),
            primary_source_record=_source_record(parent.source_lineage),
        )

    release_values = {
        field.name: getattr(composition.release_inputs, field.name)
        for field in fields(CanonicalReleaseInputs)
    }
    release_values["captured_contents"] = (exclusion,)
    with pytest.raises(ReleaseContractError):
        CanonicalReleaseInputs(**release_values)
    _assert_no_forbidden_sentinels(exclusion, composition.preview)


def test_metric_evidence_validation_dedupe_and_capture_boundaries_are_composed(
    monkeypatch,
):
    composition = _assemble_happy()
    validator = getattr(
        google_normalization,
        "validate_and_canonicalize_evidence_url",
        None,
    )
    assert callable(validator)
    validator_calls = []

    def tracking_validator(*args, **kwargs):
        validator_calls.append((args, kwargs))
        return validator(*args, **kwargs)

    monkeypatch.setattr(
        google_normalization,
        "validate_and_canonicalize_evidence_url",
        tracking_validator,
    )
    oral_source = MetricSourceCells(
        metric_id=MetricId("MET-0016"),
        metric_type="Synthetic metric",
        indicator="Synthetic indicator",
        approved_statement=ORAL_SENTINEL,
        note="不留文字紀錄",
        maintenance_updated_at=date(2026, 8, 11),
        evidence_urls=(
            f"https://evidence.example/item?access_token={TOKEN_SENTINEL}",
        ),
        channel_cells=(),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        can_quote_externally=False,
        source_lineage=composition.canonical_lineage,
    )

    exclusion = minimize_public_metric_source(oral_source)

    assert type(exclusion) is ExcludedSourceRef
    assert validator_calls == []

    safe_raw_urls = (
        f"https://evidence.example/item?utm_source={RAW_URL_SENTINEL}",
        "https://evidence.example/item",
        "https://evidence.example/second",
    )
    eligible = minimize_public_metric_source(
        _written_metric_source(composition.canonical_lineage, safe_raw_urls)
    )
    metric = create_public_metric(eligible)
    expected_evidence = (
        "https://evidence.example/item",
        "https://evidence.example/second",
    )
    assert [call[0][0] for call in validator_calls] == list(safe_raw_urls)
    assert eligible.evidence_urls == expected_evidence
    assert metric.evidence_urls == expected_evidence

    asset_urls = tuple(
        candidate.canonical_url.value
        for candidate in composition.asset_resolution.candidates
    )
    captured_ids = tuple(
        item.captured_content_id for item in composition.release_inputs.captured_contents
    )
    assert all(url not in asset_urls for url in expected_evidence)
    assert captured_ids == (composition.captured_content.captured_content_id,)

    unsafe_url = (
        "https://evidence.example/item?access_token="
        f"{TOKEN_SENTINEL}"
    )
    with pytest.raises(MetricMinimizationError) as caught:
        minimize_public_metric_source(
            _written_metric_source(
                composition.canonical_lineage,
                ("https://evidence.example/item", unsafe_url),
            )
        )

    assert str(caught.value) == "EVIDENCE_URL_UNSAFE"
    _assert_no_forbidden_sentinels(
        exclusion,
        eligible,
        metric,
        caught.value,
        composition.asset_resolution,
        composition.capture_request,
        composition.captured_content,
        composition.chunks,
        composition.preview,
        composition.preview_json,
        composition.preview_markdown,
        composition.manifest,
        composition.manifest_bytes,
    )


def test_same_canonical_url_dedupes_four_wp6_sources_before_capture():
    _, resolved, resolution = _resolve_snapshot(_snapshot())

    extracted = extract_link_candidates(
        EligibleAssetLinkCell(resolved, AssetSourceSlot.ARTICLE)
    )
    assert [candidate.source for candidate in extracted] == [
        LinkSource.RICH_TEXT,
        LinkSource.CELL_HYPERLINK,
        LinkSource.HYPERLINK_FORMULA,
        LinkSource.LITERAL_TEXT,
    ]
    assert resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert len(resolution.candidates) == 1
    assert len(resolution.candidates[0].provenance_occurrences) == 4


def test_resolved_preview_requires_explicit_diff_authority():
    source_fingerprint, _, resolution = _resolve_snapshot(_snapshot())

    with pytest.raises(PreviewContractError) as error:
        _preview_for(
            source_fingerprint=source_fingerprint,
            resolutions=(resolution,),
            diff_decisions=(),
        )
    assert error.value.code == "RESOLVED_DIFF_DECISION_REQUIRED"


def test_distinct_urls_render_needs_review_and_prevent_partial_release():
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    snapshot = _snapshot(
        additional_article_cells=(
            _article_cell(
                article_row=7,
                rich_url="https://example.test/different-story",
            ),
        ),
    )
    captured_a = _assemble_pre_release(
        snapshot=snapshot,
        article_row=6,
        source_row_count=2,
    )
    source_fingerprint, _, needs_review = _resolve_snapshot(
        captured_a.snapshot,
        article_row=7,
        asset_key=SECOND_ASSET_KEY,
    )
    resolved_a = captured_a.asset_resolution

    assert resolved_a.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert needs_review.status is AssetResolutionStatus.NEEDS_REVIEW
    assert len(needs_review.candidates) == 2
    assert not hasattr(needs_review, "winner")
    assert (
        captured_a.source_fingerprint
        == source_fingerprint
        == resolved_a.lineage.source_fingerprint
        == needs_review.lineage.source_fingerprint
    )
    assert {
        resolved_a.lineage.sync_batch_id,
        needs_review.lineage.sync_batch_id,
        captured_a.release_inputs.metadata_sync_batch_id,
    } == {BATCH}
    assert {
        resolved_a.lineage.source_coordinate,
        needs_review.lineage.source_coordinate,
    } == {(6, 7), (7, 7)}
    assert {resolved_a.asset_key, needs_review.asset_key} == {
        ASSET_KEY,
        SECOND_ASSET_KEY,
    }

    preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(resolved_a, needs_review),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
    )
    assert preview.source_fingerprint == source_fingerprint
    assert {
        (item.asset_key, item.status, item.source_row)
        for item in preview.items
        if item.asset_key is not None
    } == {
        (ASSET_KEY, PreviewStatus.CREATE, 7),
        (SECOND_ASSET_KEY, PreviewStatus.NEEDS_REVIEW, 8),
    }
    assert any(
        issue.severity is ValidationSeverity.NEEDS_REVIEW
        and issue.asset_key == SECOND_ASSET_KEY
        for issue in preview.issues
    )
    assert render_preview_json(preview)
    assert render_preview_markdown(preview)
    assert {
        item.asset_key
        for item in captured_a.release_inputs.captured_contents
        if item.asset_key is not None
    } == {ASSET_KEY}
    assert not hasattr(captured_a, "manifest")
    assert calls == []

    # The caller sees the complete A+B batch and does not submit its A-only
    # candidate material while unresolved B remains.
    manifest = _compose_complete_candidate(
        asset_resolutions=(resolved_a, needs_review),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY, SECOND_ASSET_KEY),
        captured_contents=(captured_a.captured_content,),
        capture_policy_associations=(
            (
                captured_a.captured_content.captured_content_id,
                captured_a.capture_policy_decision,
            ),
        ),
        release_inputs=captured_a.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_title_without_url_renders_incomplete_and_builds_no_release():
    happy = _assemble_happy()
    source_fingerprint, _, incomplete = _resolve_snapshot(_snapshot(title_only=True))
    preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(incomplete,),
        diff_decisions=(),
    )

    assert incomplete.status is AssetResolutionStatus.INCOMPLETE
    assert incomplete.candidates == ()
    assert any(item.status is PreviewStatus.INCOMPLETE for item in preview.items)
    assert any(
        issue.severity is ValidationSeverity.NEEDS_REVIEW
        for issue in preview.issues
    )
    assert render_preview_json(preview)
    assert render_preview_markdown(preview)
    _assert_not_submitted_to_wp14(
        resolutions=(incomplete,),
        preview=preview,
        expected_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        policy_associations=(),
        release_inputs=happy.release_inputs,
    )


def test_unsafe_url_keeps_only_rejection_codes_and_blocks_capture_and_release():
    happy = _assemble_happy()
    unsafe_url = (
        "https://example.test/private?token="
        f"{TOKEN_SENTINEL}&ref={RAW_URL_SENTINEL}"
    )
    source_fingerprint, _, blocked_resolution = _resolve_snapshot(
        _snapshot(
            rich_url=unsafe_url,
            cell_url=unsafe_url,
            formula_url=unsafe_url,
            literal_text=unsafe_url,
        )
    )
    preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(blocked_resolution,),
        diff_decisions=(),
    )
    blocked_decision = evaluate_capture_policy(
        CapturePolicy(policy_version=POLICY_VERSION),
        DomainClass.UNSAFE_PRIVATE_OR_INTERNAL,
    )

    assert blocked_resolution.status is AssetResolutionStatus.INCOMPLETE
    assert blocked_resolution.candidates == ()
    assert {
        item.rejection_code for item in blocked_resolution.rejected_occurrences
    } == {URLRejectionCode.SENSITIVE_QUERY}
    assert blocked_decision.mode is CaptureMode.BLOCKED
    with pytest.raises(CapturePolicyError) as error:
        CaptureRequest(
            ValidatedCaptureTargetRef("synthetic-blocked-target"),
            blocked_decision,
        )
    _assert_not_submitted_to_wp14(
        resolutions=(blocked_resolution,),
        preview=preview,
        expected_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        policy_associations=(
            (happy.captured_content.captured_content_id, blocked_decision),
        ),
        release_inputs=happy.release_inputs,
    )
    _assert_no_forbidden_sentinels(
        blocked_resolution,
        preview,
        render_preview_json(preview),
        render_preview_markdown(preview),
        error.value,
    )


@pytest.mark.parametrize(
    ("domain_class", "expected_mode"),
    [
        (DomainClass.UNKNOWN_THIRD_PARTY, CaptureMode.METADATA_ONLY),
        (DomainClass.AUTHENTICATED_OR_PAYWALLED, CaptureMode.BLOCKED),
    ],
)
def test_non_full_text_policy_decisions_prevent_complete_submission(
    domain_class: DomainClass,
    expected_mode: CaptureMode,
):
    happy = _assemble_happy()
    decision = evaluate_capture_policy(
        CapturePolicy(policy_version=POLICY_VERSION),
        domain_class,
    )
    release_inputs = _release_inputs(
        source_fingerprint=happy.source_fingerprint,
        captured_content=happy.captured_content,
        chunks=happy.chunks,
        capture_policy_decision=decision,
    )

    assert decision.mode is expected_mode
    _assert_not_submitted_to_wp14(
        resolutions=(happy.asset_resolution,),
        preview=happy.preview,
        expected_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        policy_associations=(
            (happy.captured_content.captured_content_id, decision),
        ),
        release_inputs=release_inputs,
    )


def test_blocking_preview_is_safe_to_render_but_not_publishable():
    happy = _assemble_happy()
    preview = _preview_for(
        source_fingerprint=happy.source_fingerprint,
        resolutions=(happy.asset_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
        validation_issues=(
            RedactedValidationIssueInput(
                severity=ValidationSeverity.BLOCKING_ERROR,
                reason_code=ValidationReasonCode("SCHEMA_MISMATCH"),
                sheet_id=SHEET_ID,
                source_row=7,
                field=PreviewField.ARTICLE,
                asset_key=ASSET_KEY,
                metric_id=None,
            ),
        ),
    )

    assert "blocking_error" in render_preview_json(preview)
    assert "blocking_error" in render_preview_markdown(preview)
    _assert_not_submitted_to_wp14(
        resolutions=(happy.asset_resolution,),
        preview=preview,
        expected_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        policy_associations=(
            (
                happy.captured_content.captured_content_id,
                happy.capture_policy_decision,
            ),
        ),
        release_inputs=happy.release_inputs,
    )


def test_complete_caller_gate_invokes_wp14_once_for_happy_batch():
    happy = _assemble_happy()
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    manifest = _compose_complete_candidate(
        asset_resolutions=(happy.asset_resolution,),
        preview=happy.preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        capture_policy_associations=(
            (
                happy.captured_content.captured_content_id,
                happy.capture_policy_decision,
            ),
        ),
        release_inputs=happy.release_inputs,
        release_builder=release_builder,
    )
    assert manifest == happy.manifest
    assert calls == [happy.release_inputs]


def test_complete_caller_gate_binds_resolution_url_to_primary_parent():
    other_url = "https://example.test/other-story"
    snapshot = _snapshot(
        additional_article_cells=(
            _article_cell(
                article_row=7,
                rich_url=other_url,
                cell_url=other_url,
                formula_url=other_url,
                literal_text=other_url,
            ),
        ),
    )
    captured = _assemble_pre_release(
        snapshot=snapshot,
        article_row=6,
        source_row_count=2,
    )
    source_fingerprint, _, other_resolution = _resolve_snapshot(
        snapshot,
        article_row=7,
        asset_key=ASSET_KEY,
    )
    mismatched_parent = _captured_content_with_canonical_url(
        captured.captured_content,
        other_resolution.candidates[0].canonical_url,
    )
    release_inputs = _release_inputs(
        source_fingerprint=source_fingerprint,
        captured_content=mismatched_parent,
        chunks=captured.chunks,
        capture_policy_decision=captured.capture_policy_decision,
        source_row_count=2,
    )
    preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(captured.asset_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    assert captured.source_fingerprint == source_fingerprint
    assert captured.asset_resolution.asset_key == mismatched_parent.asset_key
    assert (
        captured.asset_resolution.candidates[0].canonical_url
        != mismatched_parent.canonical_url
    )
    assert (
        captured.asset_resolution.lineage.sheet_id,
        captured.asset_resolution.lineage.source_row_index + 1,
    ) == (
        mismatched_parent.source_lineage.sheet_id,
        mismatched_parent.source_lineage.source_row,
    )

    manifest = _compose_complete_candidate(
        asset_resolutions=(captured.asset_resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(mismatched_parent,),
        capture_policy_associations=(
            (
                mismatched_parent.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_binds_resolution_location_to_primary_parent():
    snapshot = _snapshot(
        additional_article_cells=(_article_cell(article_row=7),),
    )
    captured = _assemble_pre_release(
        snapshot=snapshot,
        article_row=6,
        source_row_count=2,
    )
    source_fingerprint, _, moved_resolution = _resolve_snapshot(
        snapshot,
        article_row=7,
        asset_key=ASSET_KEY,
    )
    moved_preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(moved_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=8,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    assert captured.source_fingerprint == source_fingerprint
    assert (
        captured.asset_resolution.candidates[0].canonical_url
        == moved_resolution.candidates[0].canonical_url
        == captured.captured_content.canonical_url
    )
    assert moved_resolution.lineage.source_coordinate == (7, 7)
    assert captured.captured_content.source_lineage.source_row == 7

    manifest = _compose_complete_candidate(
        asset_resolutions=(moved_resolution,),
        preview=moved_preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(captured.captured_content,),
        capture_policy_associations=(
            (
                captured.captured_content.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=captured.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_binds_preview_location_to_resolution():
    snapshot = _snapshot(
        additional_article_cells=(_article_cell(article_row=7),),
    )
    captured = _assemble_pre_release(
        snapshot=snapshot,
        article_row=6,
        source_row_count=2,
    )
    source_fingerprint, _, moved_resolution = _resolve_snapshot(
        snapshot,
        article_row=7,
        asset_key=ASSET_KEY,
    )
    moved_preview = _preview_for(
        source_fingerprint=source_fingerprint,
        resolutions=(moved_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=8,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    assert captured.asset_resolution.lineage.source_coordinate == (6, 7)
    assert captured.captured_content.source_lineage.source_row == 7
    assert {
        (item.asset_key, item.status, item.sheet_id, item.source_row)
        for item in moved_preview.items
    } == {(ASSET_KEY, PreviewStatus.CREATE, SHEET_ID, 8)}

    manifest = _compose_complete_candidate(
        asset_resolutions=(captured.asset_resolution,),
        preview=moved_preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(captured.captured_content,),
        capture_policy_associations=(
            (
                captured.captured_content.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=captured.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_binds_preview_rejection_count_and_warning():
    snapshot = _snapshot(
        rich_url="https://example.test/private?token=synthetic-rejection",
    )
    captured = _assemble_pre_release(snapshot=snapshot)
    actual_resolution = captured.asset_resolution
    preview_resolution = replace(
        actual_resolution,
        rejected_occurrences=(),
    )
    preview = _preview_for(
        source_fingerprint=captured.source_fingerprint,
        resolutions=(preview_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    preview_item = next(
        item for item in preview.items if item.asset_key == ASSET_KEY
    )
    actual_rejection_codes = {
        occurrence.rejection_code
        for occurrence in actual_resolution.rejected_occurrences
    }
    preview_rejection_projection = {
        (issue.reason.code, issue.severity)
        for issue in preview.issues
        if issue.asset_key == ASSET_KEY
        and issue.reason.domain is PreviewReasonDomain.URL_REJECTION
    }
    assert actual_resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE
    assert len(actual_resolution.candidates) == 1
    assert actual_rejection_codes == {URLRejectionCode.SENSITIVE_QUERY}
    assert len(actual_resolution.rejected_occurrences) == 1
    assert preview_item.candidate_count == 1
    assert preview_item.rejected_count == 0
    assert preview_rejection_projection == set()

    manifest = _compose_complete_candidate(
        asset_resolutions=(actual_resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(captured.captured_content,),
        capture_policy_associations=(
            (
                captured.captured_content.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=captured.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_binds_preview_url_rejection_codes():
    snapshot = _snapshot(
        rich_url="https://example.test/private?token=synthetic-rejection",
        cell_url="http://localhost/story",
    )
    captured = _assemble_pre_release(snapshot=snapshot)
    base_resolution = captured.asset_resolution
    rejections_by_code = {
        occurrence.rejection_code: occurrence
        for occurrence in base_resolution.rejected_occurrences
    }
    actual_resolution = replace(
        base_resolution,
        rejected_occurrences=(
            rejections_by_code[URLRejectionCode.SENSITIVE_QUERY],
        ),
    )
    preview_resolution = replace(
        base_resolution,
        rejected_occurrences=(
            rejections_by_code[URLRejectionCode.LOCAL_HOST_NOT_ALLOWED],
        ),
    )
    preview = _preview_for(
        source_fingerprint=captured.source_fingerprint,
        resolutions=(preview_resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    preview_item = next(
        item for item in preview.items if item.asset_key == ASSET_KEY
    )
    actual_codes = {
        occurrence.rejection_code.value
        for occurrence in actual_resolution.rejected_occurrences
    }
    preview_codes = {
        issue.reason.code
        for issue in preview.issues
        if issue.asset_key == ASSET_KEY
        and issue.reason.domain is PreviewReasonDomain.URL_REJECTION
    }
    assert preview_item.rejected_count == len(
        actual_resolution.rejected_occurrences
    )
    assert actual_codes == {URLRejectionCode.SENSITIVE_QUERY.value}
    assert preview_codes == {URLRejectionCode.LOCAL_HOST_NOT_ALLOWED.value}

    manifest = _compose_complete_candidate(
        asset_resolutions=(actual_resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(captured.captured_content,),
        capture_policy_associations=(
            (
                captured.captured_content.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=captured.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_preserves_duplicate_rejection_occurrences():
    captured = _assemble_pre_release(
        snapshot=_snapshot(
            rich_url="https://example.test/private?token=synthetic-rejection-a",
            cell_url="https://example.test/private?token=synthetic-rejection-b",
        )
    )
    resolution = captured.asset_resolution
    preview = _preview_for(
        source_fingerprint=captured.source_fingerprint,
        resolutions=(resolution,),
        diff_decisions=(
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    preview_item = next(
        item for item in preview.items if item.asset_key == ASSET_KEY
    )
    rejection_projection = {
        (issue.reason.code, issue.severity)
        for issue in preview.issues
        if issue.asset_key == ASSET_KEY
        and issue.reason.domain is PreviewReasonDomain.URL_REJECTION
    }
    assert len(resolution.rejected_occurrences) == 2
    assert preview_item.rejected_count == 2
    assert rejection_projection == {
        (URLRejectionCode.SENSITIVE_QUERY.value, ValidationSeverity.WARNING)
    }

    manifest = _compose_complete_candidate(
        asset_resolutions=(resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(captured.captured_content,),
        capture_policy_associations=(
            (
                captured.captured_content.captured_content_id,
                captured.capture_policy_decision,
            ),
        ),
        release_inputs=captured.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is not None
    assert calls == [captured.release_inputs]


def test_complete_caller_gate_binds_preview_policy_version():
    happy = _assemble_happy()
    preview = build_preview(
        PreviewBuildContext(
            source_fingerprint=happy.source_fingerprint,
            policy_version="wp16-other-policy-v1",
            normalized_hash=NORMALIZED_HASH,
        ),
        (happy.oral_exclusion,),
        (happy.asset_resolution,),
        (
            PreviewDiffDecision(
                asset_key=ASSET_KEY,
                status=PreviewStatus.CREATE,
                sheet_id=SHEET_ID,
                source_row=7,
            ),
        ),
        (),
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    assert preview.policy_version != happy.capture_policy_decision.policy_version
    assert render_preview_json(preview)
    assert render_preview_markdown(preview)

    manifest = _compose_complete_candidate(
        asset_resolutions=(happy.asset_resolution,),
        preview=preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(happy.captured_content,),
        capture_policy_associations=(
            (
                happy.captured_content.captured_content_id,
                happy.capture_policy_decision,
            ),
        ),
        release_inputs=happy.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_complete_caller_gate_rejects_cross_composition_release_inputs():
    witness = _assemble_happy(article_row=6)
    submitted = _assemble_happy(article_row=7)
    batch_values = {
        field.name: getattr(witness.release_inputs, field.name)
        for field in fields(CanonicalReleaseInputs)
    }
    batch_values["metadata_sync_batch_id"] = "SYNTHETIC-WP16-OTHER-BATCH"
    batch_mismatch = CanonicalReleaseInputs(**batch_values)
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    assert witness.source_fingerprint != submitted.source_fingerprint
    assert batch_mismatch.metadata_sync_batch_id != BATCH
    for release_inputs in (submitted.release_inputs, batch_mismatch):
        manifest = _compose_complete_candidate(
            asset_resolutions=(witness.asset_resolution,),
            preview=witness.preview,
            expected_current_asset_keys=(ASSET_KEY,),
            captured_contents=(witness.captured_content,),
            capture_policy_associations=(
                (
                    witness.captured_content.captured_content_id,
                    witness.capture_policy_decision,
                ),
            ),
            release_inputs=release_inputs,
            release_builder=release_builder,
        )
        assert manifest is None
    assert calls == []


def test_complete_caller_gate_binds_parent_and_policy_witnesses():
    happy = _assemble_happy(article_row=6)
    other_parent = _stale_captured_content(
        happy.captured_content,
        None,
        last_attempt=STALE_ATTEMPT,
    )
    other_policy = evaluate_capture_policy(
        CapturePolicy(
            policy_version="synthetic-wp16-other-policy-v1",
            approved_domain_rules=(
                ApprovedDomainRule("example.test", CaptureMode.FULL_TEXT),
            ),
        ),
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key="example.test",
    )
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    for captured_contents, policy_associations in (
        (
            (other_parent,),
            ((other_parent.captured_content_id, happy.capture_policy_decision),),
        ),
        (
            (happy.captured_content,),
            ((happy.captured_content.captured_content_id, other_policy),),
        ),
    ):
        manifest = _compose_complete_candidate(
            asset_resolutions=(happy.asset_resolution,),
            preview=happy.preview,
            expected_current_asset_keys=(ASSET_KEY,),
            captured_contents=captured_contents,
            capture_policy_associations=policy_associations,
            release_inputs=happy.release_inputs,
            release_builder=release_builder,
        )
        assert manifest is None

    assert calls == []


def test_resolved_asset_without_captured_parent_is_not_submitted_to_wp14():
    happy = _assemble_happy()
    calls: list[CanonicalReleaseInputs] = []

    def release_builder(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
        calls.append(inputs)
        return build_release_manifest(inputs)

    manifest = _compose_complete_candidate(
        asset_resolutions=(happy.asset_resolution,),
        preview=happy.preview,
        expected_current_asset_keys=(ASSET_KEY,),
        captured_contents=(),
        capture_policy_associations=(),
        release_inputs=happy.release_inputs,
        release_builder=release_builder,
    )
    assert manifest is None
    assert calls == []


def test_valid_stale_lkg_builds_stale_chunk_and_candidate_manifest():
    stale = _compose_stale(_assemble_happy())

    assert stale.eligibility_result.eligible is True
    assert stale.eligibility_result.reason is LkgEligibilityReason.ELIGIBLE
    assert stale.candidate.capture_status is CaptureStatus.STALE
    assert not hasattr(stale.candidate, "clean_body")
    assert stale.captured_content.capture_status is CaptureStatus.STALE
    assert all(
        chunk.metadata.capture_status is CaptureStatus.STALE
        for chunk in stale.chunks
    )
    assert stale.manifest.publish_state is ReleasePublishState.CANDIDATE
    assert stale.manifest.captured_revisions[0].capture_status is CaptureStatus.STALE
    assert stale.manifest.captured_revisions[0].freshness_policy_version == (
        "synthetic-wp16-freshness-v1"
    )
    assert not hasattr(stale.manifest, "active_pointer")


def test_expired_stale_proof_cannot_form_chunk_or_complete_manifest():
    happy = _assemble_happy()
    valid_stale = _compose_stale(happy)
    valid_inputs = _release_inputs(
        source_fingerprint=happy.source_fingerprint,
        captured_content=valid_stale.captured_content,
        chunks=valid_stale.chunks,
        capture_policy_decision=happy.capture_policy_decision,
        stale_proofs=(
            (
                valid_stale.captured_content.captured_content_id,
                valid_stale.eligibility_input,
                valid_stale.eligibility_result,
            ),
        ),
    )
    assert build_release_manifest(valid_inputs) == valid_stale.manifest

    invalid_input = LkgEligibilityInput(
        current_canonical_url=happy.captured_content.canonical_url,
        previous_success=happy.captured_content,
        current_capture_policy=happy.capture_policy_decision,
        current_failure_category=classify_fetch_failure(FetchFailureReason.TIMEOUT),
        governance_allowed=True,
        identity_reconciled=True,
        freshness_policy=ApprovedLkgFreshnessPolicy(
            policy_version="synthetic-wp16-freshness-v1",
            max_age=timedelta(hours=1),
        ),
        current_attempt_at=STALE_ATTEMPT,
    )
    invalid_result = evaluate_lkg_reuse(invalid_input)

    assert invalid_result.eligible is False
    assert invalid_result.reason is LkgEligibilityReason.FRESHNESS_EXPIRED
    with pytest.raises(ContentHashingError, match="LKG_RESULT_NOT_ELIGIBLE"):
        compose_stale_lkg(invalid_input, invalid_result)

    structural_stale = _stale_captured_content(
        happy.captured_content,
        None,
        last_attempt=STALE_ATTEMPT,
    )
    with pytest.raises(CapturedChunkError):
        _build_chunks(
            structural_stale,
            stale_input=invalid_input,
            stale_result=invalid_result,
        )
    invalid_inputs = _release_inputs(
        source_fingerprint=happy.source_fingerprint,
        captured_content=valid_stale.captured_content,
        chunks=valid_stale.chunks,
        capture_policy_decision=happy.capture_policy_decision,
        stale_proofs=(
            (
                valid_stale.captured_content.captured_content_id,
                invalid_input,
                invalid_result,
            ),
        ),
    )
    for field in fields(CanonicalReleaseInputs):
        if field.name != "stale_proofs":
            assert getattr(valid_inputs, field.name) == getattr(invalid_inputs, field.name)

    with pytest.raises(ReleaseContractError) as error:
        build_release_manifest(invalid_inputs)
    assert error.value.code == "STALE_PROOF_INVALID"


def test_complete_composition_replay_is_deterministic():
    first = _assemble_happy()
    second = _assemble_happy()

    assert first.source_bytes == second.source_bytes
    assert first.source_fingerprint == second.source_fingerprint
    assert first.link_cell == second.link_cell
    assert first.canonical_lineage == second.canonical_lineage
    assert first.oral_exclusion == second.oral_exclusion
    assert first.asset_resolution == second.asset_resolution
    assert first.capture_policy_decision == second.capture_policy_decision
    assert first.capture_request == second.capture_request
    assert first.normalization == second.normalization
    assert first.capture_hash == second.capture_hash
    assert first.captured_content == second.captured_content
    assert first.chunks == second.chunks
    assert first.manifest_bytes == second.manifest_bytes
    assert first.manifest_hash == second.manifest_hash
    assert first.preview == second.preview
    assert first.preview_json == second.preview_json
    assert first.preview_markdown == second.preview_markdown


def test_collection_reorder_is_not_logical_row_reorder():
    original = _assemble_happy()
    collection_reordered = _assemble_happy(reverse_cells=True)

    assert original.snapshot != collection_reordered.snapshot
    assert original.source_bytes == collection_reordered.source_bytes
    assert original.source_fingerprint == collection_reordered.source_fingerprint
    assert original.link_cell == collection_reordered.link_cell
    assert original.asset_resolution == collection_reordered.asset_resolution
    assert original.captured_content == collection_reordered.captured_content
    assert original.chunks == collection_reordered.chunks
    assert original.manifest_bytes == collection_reordered.manifest_bytes
    assert original.preview == collection_reordered.preview

    chunk_reordered_manifest = build_release_manifest(
        _release_inputs(
            source_fingerprint=original.source_fingerprint,
            captured_content=original.captured_content,
            chunks=tuple(reversed(original.chunks)),
            capture_policy_decision=original.capture_policy_decision,
        )
    )
    assert serialize_release_manifest(chunk_reordered_manifest) == (
        original.manifest_bytes
    )
    assert chunk_reordered_manifest.captured_revisions[0].chunk_set_hash == (
        original.manifest.captured_revisions[0].chunk_set_hash
    )


def test_logical_row_reorder_changes_lineage_not_canonical_identity():
    original = _assemble_happy(article_row=6)
    moved = _assemble_happy(article_row=7)

    assert original.asset_resolution.asset_key == moved.asset_resolution.asset_key
    assert original.asset_resolution.asset_key.source_record_id == (
        moved.asset_resolution.asset_key.source_record_id
    )
    assert original.oral_exclusion.metric_id == moved.oral_exclusion.metric_id
    assert original.capture_policy_decision == moved.capture_policy_decision
    assert original.normalization.clean_body == moved.normalization.clean_body
    assert original.capture_hash == moved.capture_hash
    assert original.captured_content.captured_content_id == (
        moved.captured_content.captured_content_id
    )
    assert [chunk.metadata.chunk_id for chunk in original.chunks] == [
        chunk.metadata.chunk_id for chunk in moved.chunks
    ]
    assert original.manifest.captured_revisions[0].chunk_set_hash == (
        moved.manifest.captured_revisions[0].chunk_set_hash
    )
    assert [
        (item.status, item.field, item.asset_key, item.metric_id)
        for item in original.preview.items
    ] == [
        (item.status, item.field, item.asset_key, item.metric_id)
        for item in moved.preview.items
    ]
    assert original.preview.normalized_hash == moved.preview.normalized_hash == NORMALIZED_HASH

    assert original.source_fingerprint != moved.source_fingerprint
    assert original.canonical_lineage != moved.canonical_lineage
    assert original.captured_content != moved.captured_content
    assert original.chunks != moved.chunks
    assert original.manifest_hash != moved.manifest_hash
    assert [item.source_row for item in original.preview.items] != [
        item.source_row for item in moved.preview.items
    ]
    assert original.preview_json != moved.preview_json
    assert original.preview_markdown != moved.preview_markdown


def test_hash_domains_markdown_boundary_and_sentinel_surfaces_remain_separate():
    composition = _assemble_happy()
    chunk_ids = tuple(chunk.metadata.chunk_id for chunk in composition.chunks)
    chunk_set_hash = composition.manifest.captured_revisions[0].chunk_set_hash

    assert type(composition.source_fingerprint) is str
    assert composition.source_fingerprint.startswith("sha256:")
    assert type(composition.capture_hash) is CaptureContentHash
    assert str(composition.capture_hash).startswith("sha256:")
    assert all(type(chunk_id) is CapturedChunkId for chunk_id in chunk_ids)
    assert all(str(chunk_id).startswith("chk:v1:sha256:") for chunk_id in chunk_ids)
    assert type(chunk_set_hash) is ChunkSetHash
    assert str(chunk_set_hash).startswith("chunkset:v1:sha256:")
    assert type(composition.manifest_hash) is ReleaseManifestHash
    assert str(composition.manifest_hash).startswith("release-manifest:v1:sha256:")
    assert NORMALIZED_HASH.startswith("sha256:")
    assert NORMALIZED_HASH != composition.source_fingerprint
    assert str(composition.capture_hash) != composition.source_fingerprint

    assert composition.captured_content.clean_body == composition.normalization.clean_body
    assert composition.preview_markdown != composition.captured_content.clean_body
    assert composition.preview_json != composition.captured_content.clean_body
    assert PUBLIC_BODY_SENTINEL in composition.normalization.clean_body
    assert PUBLIC_BODY_SENTINEL in composition.captured_content.clean_body
    assert any(PUBLIC_BODY_SENTINEL in chunk.text for chunk in composition.chunks)
    assert PUBLIC_BODY_SENTINEL not in composition.manifest_bytes.decode("utf-8")
    assert PUBLIC_BODY_SENTINEL not in repr(composition.preview)
    assert PUBLIC_BODY_SENTINEL not in composition.preview_json
    assert PUBLIC_BODY_SENTINEL not in composition.preview_markdown

    # Preview renderer output is never an authority input to WP12, WP13, or WP14.
    with pytest.raises(ContentHashingError):
        compute_capture_content_hash(composition.preview_markdown)
    parent = composition.captured_content
    assert parent.content_hash is not None
    assert parent.parser_version is not None
    with pytest.raises(CapturedChunkError):
        build_captured_chunk(
            captured_content=composition.preview_markdown,
            revision_ref=CaptureRevisionRef(
                captured_content_id=parent.captured_content_id,
                content_hash=CaptureContentHash(parent.content_hash),
                parser_version=parent.parser_version,
            ),
            span=SyntheticChunkSpan(
                text="preview",
                start=0,
                end=7,
                section_anchor=SectionAnchor("preview-boundary-probe"),
                section_heading=None,
                ordinal=0,
            ),
        )
    release_values = {
        field.name: getattr(composition.release_inputs, field.name)
        for field in fields(CanonicalReleaseInputs)
    }
    release_values["captured_contents"] = (composition.preview,)
    with pytest.raises(ReleaseContractError):
        CanonicalReleaseInputs(**release_values)

    _assert_no_forbidden_sentinels(
        composition.canonical_lineage,
        composition.oral_exclusion,
        composition.asset_resolution,
        composition.capture_policy_decision,
        composition.capture_request,
        composition.normalization,
        composition.captured_content,
        composition.chunks,
        composition.manifest,
        composition.manifest_bytes,
        composition.preview,
        composition.preview_json,
        composition.preview_markdown,
    )
