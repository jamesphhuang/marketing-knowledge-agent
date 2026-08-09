from __future__ import annotations

import json
import pickle
from dataclasses import asdict, replace
from datetime import date

import pytest

import marketing_knowledge_agent.canonical_models as canonical_models
import marketing_knowledge_agent.google_normalization as google_normalization
from marketing_knowledge_agent.canonical_models import (
    CanonicalSourceLineage,
    ExposureChannel,
    LifecycleStatus,
    MetricId,
    PublicMetric,
    PublishEligibility,
    ReviewStatus,
    create_public_metric,
)
from marketing_knowledge_agent.cell_normalization import (
    FieldContract,
    FieldValueKind,
    InheritanceReason,
    ResolvedCellValue,
    SourceFieldLineage,
    SourceLineage,
    ValueSource,
)
from marketing_knowledge_agent.google_normalization import (
    ExcludedSourceRef,
    ExclusionReason,
    MetricMinimizationError,
    MetricSourceCells,
    PersistenceEligibleMetricInput,
    minimize_public_metric_source,
)
from marketing_knowledge_agent.sheets_contracts import (
    CellData,
    DataValidation,
    DataValidationCondition,
    GoogleValue,
    GridRange,
)


SENTINEL = "SYNTHETIC_ORAL_ONLY_SENTINEL_7F3A"
WRITTEN_CHANNELS = (
    ExposureChannel.PRESS_RELEASE,
    ExposureChannel.OWNED_MEDIA,
    ExposureChannel.SALESKITS,
    ExposureChannel.SPEAKING_DECK,
    ExposureChannel.WEBSITE_RECRUITING,
    ExposureChannel.ADS,
)
ALL_SOURCE_CHANNELS = (
    ExposureChannel.PRESS_RELEASE,
    ExposureChannel.OWNED_MEDIA,
    ExposureChannel.SALESKITS,
    ExposureChannel.VERBAL_BRIEFING,
    ExposureChannel.SPEAKING_DECK,
    ExposureChannel.WEBSITE_RECRUITING,
    ExposureChannel.ADS,
)
USER_APPROVED_NOTE_MARKERS = (
    "不留文字紀錄",
    "不可書面",
    "禁止書面",
    "僅用於口頭",
    "只用於口頭",
    "不可留書面",
    "只能口頭",
    "不要留文字",
)


def _lineage() -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=101,
        sheet_title="Synthetic Public Metrics",
        source_row=7,
        source_columns={"approved_statement": "C", "note": "D"},
        source_ranges={"approved_statement": "C7", "note": "D7"},
        source_fingerprint="sha256:synthetic-source-fingerprint",
        sync_batch_id="SYNTHETIC-WP5-BATCH",
    )


def _channel_cell(
    channel: ExposureChannel,
    value: object,
    *,
    value_kind: FieldValueKind = FieldValueKind.BOOLEAN,
    validation_type: str | None = "BOOLEAN",
) -> ResolvedCellValue:
    column_index = ALL_SOURCE_CHANNELS.index(channel) + 6
    validation = (
        DataValidation(
            condition=DataValidationCondition(condition_type=validation_type)
        )
        if validation_type is not None
        else None
    )
    effective_value = GoogleValue(bool_value=value) if type(value) is bool else None
    source_cell = CellData(
        row_index=6,
        column_index=column_index,
        formatted_value=str(value) if value is not None else None,
        effective_value=effective_value,
        data_validation=validation,
    )
    return ResolvedCellValue(
        normalized_value=value,
        display_value=str(value) if value is not None else None,
        value_source=(ValueSource.EFFECTIVE_VALUE if value is not None else ValueSource.BLANK),
        source_was_formula=False,
        source_cell=source_cell,
        value_cell=source_cell,
        field_contract=FieldContract(
            field_name=channel.value,
            value_kind=value_kind,
            source_column_index=column_index,
        ),
        lineage=SourceLineage(
            spreadsheet_id="synthetic-spreadsheet-wp5",
            sheet_id=101,
            sheet_title="Synthetic Public Metrics",
            sheet_hidden=False,
            source_row_index=6,
            source_column_index=column_index,
            source_fingerprint="sha256:synthetic-source-fingerprint",
            sync_batch_id="SYNTHETIC-WP5-BATCH",
        ),
        field_lineage=SourceFieldLineage(
            field_name=channel.value,
            target_row_index=6,
            target_column_index=column_index,
            value_row_index=6,
            value_column_index=column_index,
            merge_anchor_row_index=None,
            merge_anchor_column_index=None,
            merge_range=None,
            inherited_from_merge=False,
            inheritance_reason=InheritanceReason.LOCAL,
        ),
    )


def _channels(*, verbal: bool, written: ExposureChannel | None = None):
    values = {channel: False for channel in ALL_SOURCE_CHANNELS}
    values[ExposureChannel.VERBAL_BRIEFING] = verbal
    if written is not None:
        values[written] = True
    return tuple(_channel_cell(channel, values[channel]) for channel in ALL_SOURCE_CHANNELS)


def _copy_cell_data(cell: CellData, **update) -> CellData:
    if hasattr(cell, "model_copy"):
        return cell.model_copy(update=update)
    return cell.copy(update=update)


def _source(
    *,
    approved_statement: str = "Synthetic eligible statement",
    note: str | None = None,
    channel_cells=None,
    evidence_urls=(),
    can_quote_externally: bool = True,
    source_lineage: CanonicalSourceLineage | None = None,
) -> MetricSourceCells:
    return MetricSourceCells(
        metric_id=MetricId("MET-0001"),
        metric_type="Synthetic metric type",
        indicator="Synthetic indicator",
        approved_statement=approved_statement,
        note=note,
        maintenance_updated_at=date(2026, 8, 9),
        evidence_urls=evidence_urls,
        channel_cells=(
            channel_cells
            if channel_cells is not None
            else _channels(
                verbal=False,
                written=ExposureChannel.PRESS_RELEASE,
            )
        ),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        can_quote_externally=can_quote_externally,
        source_lineage=source_lineage if source_lineage is not None else _lineage(),
    )


def test_oral_only_is_excluded_before_any_persistence_eligible_or_public_metric_type():
    source = _source(
        approved_statement=SENTINEL,
        note=f"{SENTINEL} 僅供口頭說明",
        evidence_urls=(f"https://example.com/{SENTINEL}",),
        channel_cells=_channels(verbal=True),
    )

    result = minimize_public_metric_source(source)

    assert type(result) is ExcludedSourceRef
    assert result.reason is ExclusionReason.ORAL_ONLY
    assert result.metric_id == MetricId("MET-0001")
    assert result.sheet_id == 101
    assert result.source_row == 7
    assert not isinstance(result, PersistenceEligibleMetricInput)
    assert set(result.model_dump()) == {
        "sheet_id",
        "source_row",
        "metric_id",
        "reason",
        "source_digest",
    }


def test_oral_only_sentinel_has_zero_supported_downstream_leakage(caplog):
    source = _source(
        approved_statement=SENTINEL,
        note=f"僅用於口頭；{SENTINEL}",
        evidence_urls=(f"https://example.com/{SENTINEL}",),
        channel_cells=_channels(verbal=True),
    )
    result = minimize_public_metric_source(source)
    observed = [
        repr(source),
        str(source),
        repr(result),
        str(result),
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        json.dumps(source, default=str, ensure_ascii=False),
        caplog.text,
    ]
    for candidate in (source, {"approved_statement": SENTINEL}, object()):
        with pytest.raises(TypeError) as exc_info:
            create_public_metric(candidate)
        observed.append(str(exc_info.value))

    assert all(SENTINEL not in item for item in observed)
    assert SENTINEL.encode() not in pickle.dumps(result)


def test_metric_source_cells_repr_is_redacted_and_common_serialization_paths_fail_closed():
    source = _source(approved_statement=SENTINEL, note=SENTINEL)

    assert repr(source) == (
        "MetricSourceCells(sheet_id=101, source_row=7, content=<redacted>)"
    )
    assert str(source) == repr(source)
    assert SENTINEL not in repr(source)
    assert not hasattr(source, "model_dump")
    assert not hasattr(source, "dict")
    with pytest.raises(TypeError):
        asdict(source)
    with pytest.raises(TypeError):
        vars(source)
    with pytest.raises(TypeError):
        json.dumps(source)
    with pytest.raises(TypeError, match="METRIC_SOURCE_CELLS_SERIALIZATION_FORBIDDEN"):
        pickle.dumps(source)


@pytest.mark.parametrize(
    "note",
    USER_APPROVED_NOTE_MARKERS,
)
def test_frozen_deterministic_note_policy_excludes_even_with_written_channel(note):
    result = minimize_public_metric_source(
        _source(
            note=f"Synthetic policy: {note}",
            channel_cells=_channels(
                verbal=True,
                written=ExposureChannel.PRESS_RELEASE,
            ),
        )
    )

    assert type(result) is ExcludedSourceRef
    assert result.reason is ExclusionReason.ORAL_ONLY


def test_note_retention_policy_excludes_before_ambiguous_channel_classification():
    channels = list(_channels(verbal=True))
    channels[0] = _channel_cell(ExposureChannel.PRESS_RELEASE, None)

    result = minimize_public_metric_source(
        _source(note="不留文字紀錄", channel_cells=tuple(channels))
    )

    assert type(result) is ExcludedSourceRef
    assert result.reason is ExclusionReason.ORAL_ONLY


def test_non_text_retention_note_does_not_expand_into_fuzzy_matching():
    result = minimize_public_metric_source(
        _source(
            note="不可公開市場細節，仍可使用已核准書面版本",
            channel_cells=_channels(
                verbal=True,
                written=ExposureChannel.PRESS_RELEASE,
            ),
        )
    )

    assert type(result) is PersistenceEligibleMetricInput


def test_note_policy_is_exactly_the_user_approved_eight_markers():
    assert (
        google_normalization._WRITTEN_RETENTION_BLOCKING_NOTE_MARKERS
        == USER_APPROVED_NOTE_MARKERS
    )
    assert "不可公開" not in USER_APPROVED_NOTE_MARKERS


def test_no_source_channel_is_not_oral_only_when_quote_policy_is_explicitly_false():
    result = minimize_public_metric_source(
        _source(
            channel_cells=_channels(verbal=False),
            can_quote_externally=False,
        )
    )

    assert type(result) is PersistenceEligibleMetricInput
    assert result.allowed_exposure_channels == ()
    assert result.can_quote_externally is False


@pytest.mark.parametrize("written_channel", WRITTEN_CHANNELS)
def test_mixed_written_and_verbal_is_not_misclassified_as_oral_only(written_channel):
    result = minimize_public_metric_source(
        _source(channel_cells=_channels(verbal=True, written=written_channel))
    )

    assert type(result) is PersistenceEligibleMetricInput
    assert ExposureChannel.VERBAL_BRIEFING in result.allowed_exposure_channels
    assert written_channel in result.allowed_exposure_channels


@pytest.mark.parametrize(
    "replacement",
    [
        _channel_cell(ExposureChannel.PRESS_RELEASE, None),
        _channel_cell(
            ExposureChannel.PRESS_RELEASE,
            "TRUE",
            value_kind=FieldValueKind.TEXT,
        ),
        _channel_cell(
            ExposureChannel.PRESS_RELEASE,
            True,
            validation_type=None,
        ),
        _channel_cell(
            ExposureChannel.PRESS_RELEASE,
            True,
            validation_type="ONE_OF_LIST",
        ),
    ],
)
def test_missing_malformed_or_non_checkbox_channel_fails_closed_without_payload(replacement):
    channels = list(_channels(verbal=False))
    channels[0] = replacement
    source = _source(
        approved_statement=SENTINEL,
        channel_cells=tuple(channels),
    )

    with pytest.raises(MetricMinimizationError) as exc_info:
        minimize_public_metric_source(source)

    assert str(exc_info.value) == "METRIC_CHANNEL_GOVERNANCE_UNCERTAIN"
    assert SENTINEL not in str(exc_info.value)


def test_missing_duplicate_or_unknown_channel_set_fails_closed():
    missing = _channels(verbal=False)[:-1]
    duplicate = _channels(verbal=False)[:-1] + (_channels(verbal=False)[0],)

    for channel_cells in (missing, duplicate):
        with pytest.raises(
            MetricMinimizationError,
            match="METRIC_CHANNEL_SET_INVALID",
        ):
            minimize_public_metric_source(_source(channel_cells=channel_cells))


def test_channel_cells_from_a_different_source_row_fail_closed():
    channels = list(_channels(verbal=False))
    mismatched = channels[0]
    channels[0] = replace(
        mismatched,
        lineage=replace(mismatched.lineage, source_row_index=8),
    )

    with pytest.raises(
        MetricMinimizationError,
        match="METRIC_CHANNEL_LINEAGE_INVALID",
    ):
        minimize_public_metric_source(_source(channel_cells=tuple(channels)))


@pytest.mark.parametrize(
    "channel",
    [ExposureChannel.VERBAL_BRIEFING, ExposureChannel.PRESS_RELEASE],
)
def test_vertical_merge_derived_g_to_m_channel_fails_closed_without_payload(channel):
    channels = list(
        _channels(
            verbal=channel is ExposureChannel.VERBAL_BRIEFING,
            written=(
                ExposureChannel.PRESS_RELEASE
                if channel is ExposureChannel.PRESS_RELEASE
                else None
            ),
        )
    )
    index = ALL_SOURCE_CHANNELS.index(channel)
    cell = channels[index]
    column_index = cell.field_lineage.target_column_index
    channels[index] = replace(
        cell,
        value_cell=_copy_cell_data(cell.value_cell, row_index=5),
        field_lineage=replace(
            cell.field_lineage,
            value_row_index=5,
            merge_anchor_row_index=5,
            merge_anchor_column_index=column_index,
            merge_range=GridRange(
                sheet_id=101,
                start_row_index=5,
                end_row_index=7,
                start_column_index=column_index,
                end_column_index=column_index + 1,
            ),
            inherited_from_merge=True,
            inheritance_reason=InheritanceReason.MERGED_RANGE,
        ),
    )

    with pytest.raises(MetricMinimizationError) as exc_info:
        minimize_public_metric_source(
            _source(approved_statement=SENTINEL, channel_cells=tuple(channels))
        )

    assert str(exc_info.value) == "METRIC_CHANNEL_LINEAGE_INVALID"
    assert SENTINEL not in str(exc_info.value)


def test_same_row_horizontal_merge_derived_channel_fails_closed_without_payload():
    channels = list(_channels(verbal=True))
    index = ALL_SOURCE_CHANNELS.index(ExposureChannel.VERBAL_BRIEFING)
    cell = channels[index]
    channels[index] = replace(
        cell,
        value_cell=_copy_cell_data(cell.value_cell, column_index=6),
        field_lineage=replace(
            cell.field_lineage,
            value_column_index=6,
            merge_anchor_row_index=6,
            merge_anchor_column_index=6,
            merge_range=GridRange(
                sheet_id=101,
                start_row_index=6,
                end_row_index=7,
                start_column_index=6,
                end_column_index=10,
            ),
            inherited_from_merge=True,
            inheritance_reason=InheritanceReason.MERGED_RANGE,
        ),
    )

    with pytest.raises(MetricMinimizationError) as exc_info:
        minimize_public_metric_source(
            _source(approved_statement=SENTINEL, channel_cells=tuple(channels))
        )

    assert str(exc_info.value) == "METRIC_CHANNEL_LINEAGE_INVALID"
    assert SENTINEL not in str(exc_info.value)


@pytest.mark.parametrize("coordinate_surface", ["source", "value"])
def test_channel_source_or_value_from_wrong_column_fails_closed_without_payload(
    coordinate_surface,
):
    channels = list(_channels(verbal=False))
    cell = channels[0]
    if coordinate_surface == "source":
        channels[0] = replace(
            cell,
            source_cell=_copy_cell_data(cell.source_cell, column_index=7),
            lineage=replace(cell.lineage, source_column_index=7),
        )
    else:
        channels[0] = replace(
            cell,
            value_cell=_copy_cell_data(cell.value_cell, column_index=7),
            field_lineage=replace(cell.field_lineage, value_column_index=7),
        )

    with pytest.raises(MetricMinimizationError) as exc_info:
        minimize_public_metric_source(
            _source(approved_statement=SENTINEL, channel_cells=tuple(channels))
        )

    assert str(exc_info.value) == "METRIC_CHANNEL_LINEAGE_INVALID"
    assert SENTINEL not in str(exc_info.value)


def test_excluded_reference_digest_is_deterministic_and_contains_no_payload_or_link_surface():
    first = minimize_public_metric_source(
        _source(
            approved_statement=SENTINEL,
            note=SENTINEL,
            evidence_urls=(f"https://example.com/{SENTINEL}",),
            channel_cells=_channels(verbal=True),
        )
    )
    second = minimize_public_metric_source(
        _source(
            approved_statement=SENTINEL,
            note=SENTINEL,
            evidence_urls=(f"https://example.com/{SENTINEL}",),
            channel_cells=_channels(verbal=True),
        )
    )

    assert first.source_digest == second.source_digest
    assert first.source_digest.startswith("sha256:")
    assert SENTINEL not in first.source_digest
    forbidden_fields = {
        "approved_statement",
        "claim",
        "note",
        "evidence_urls",
        "reference_url",
        "hyperlink",
        "capture_candidate",
        "snippet",
        "body",
    }
    assert forbidden_fields.isdisjoint(first.model_dump())


def test_hostile_free_text_is_not_copied_into_excluded_output(caplog):
    hostile_lineage = CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=101,
        sheet_title=SENTINEL,
        source_row=7,
        source_columns={SENTINEL: f"C-{SENTINEL}"},
        source_ranges={SENTINEL: f"C7:{SENTINEL}"},
        source_fingerprint="sha256:synthetic-source-fingerprint",
        sync_batch_id="SYNTHETIC-WP5-BATCH",
    )
    hostile_channels = tuple(
        replace(
            cell,
            lineage=replace(cell.lineage, sheet_title=SENTINEL),
        )
        for cell in _channels(verbal=True)
    )
    source = _source(
        approved_statement=SENTINEL,
        note=SENTINEL,
        evidence_urls=(f"https://example.com/{SENTINEL}",),
        channel_cells=hostile_channels,
        source_lineage=hostile_lineage,
    )

    result = minimize_public_metric_source(source)
    with pytest.raises(TypeError) as exc_info:
        create_public_metric(result)
    dumped = result.model_dump(mode="json")
    observed = (
        repr(result),
        str(result),
        repr(dumped),
        json.dumps(dumped, ensure_ascii=False),
        str(exc_info.value),
        caplog.text,
    )

    assert type(result) is ExcludedSourceRef
    assert "sheet_title" not in dumped
    assert all(SENTINEL not in item for item in observed)
    assert SENTINEL.encode() not in pickle.dumps(result)


def test_only_minimizer_can_create_immutable_persistence_eligible_input():
    result = minimize_public_metric_source(_source())

    assert type(result) is PersistenceEligibleMetricInput
    assert result.gate_status == "wp5_persistence_eligible"
    assert "_PERSISTENCE_ELIGIBLE_GATE" not in vars(google_normalization)
    with pytest.raises(TypeError, match="PERSISTENCE_ELIGIBLE_INPUT_REQUIRES_WP5_GATE"):
        PersistenceEligibleMetricInput(
            _wp5_gate=getattr(
                google_normalization,
                "_PERSISTENCE_ELIGIBLE_GATE",
                None,
            ),
            **result.model_dump(),
        )
    with pytest.raises(TypeError, match="PERSISTENCE_ELIGIBLE_INPUT_REQUIRES_WP5_GATE"):
        PersistenceEligibleMetricInput.model_validate(result.model_dump())
    with pytest.raises((TypeError, ValueError)):
        result.approved_statement = SENTINEL
    with pytest.raises(TypeError, match="PERSISTENCE_ELIGIBLE_INPUT_REQUIRES_WP5_GATE"):
        result.model_copy(update={"approved_statement": SENTINEL})
    with pytest.raises(TypeError, match="PERSISTENCE_ELIGIBLE_INPUT_REQUIRES_WP5_GATE"):
        result.copy(update={"approved_statement": SENTINEL})


def test_exact_eligible_factory_builds_normally_validated_public_metric_without_source_retention():
    source = _source(
        approved_statement="Synthetic approved statement",
        evidence_urls=("https://example.com/synthetic-evidence",),
        channel_cells=_channels(
            verbal=True,
            written=ExposureChannel.WEBSITE_RECRUITING,
        ),
    )
    eligible = minimize_public_metric_source(source)

    metric = create_public_metric(eligible)

    assert metric.metric_id == MetricId("MET-0001")
    assert metric.approved_statement == "Synthetic approved statement"
    assert metric.evidence_urls == ("https://example.com/synthetic-evidence",)
    assert metric.allowed_exposure_channels == (
        ExposureChannel.VERBAL_BRIEFING,
        ExposureChannel.WEBSITE_RECRUITING,
    )
    assert metric.source_lineage == _lineage()
    assert source not in metric.__dict__.values()


def test_ordinary_module_import_cannot_reuse_a_public_metric_construction_token():
    eligible = minimize_public_metric_source(_source())
    assert "_PUBLIC_METRIC_WP5_CONSTRUCTION_GATE" not in vars(canonical_models)

    with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
        PublicMetric(
            _wp5_gate=getattr(
                canonical_models,
                "_PUBLIC_METRIC_WP5_CONSTRUCTION_GATE",
                None,
            ),
            **eligible.model_dump(),
        )


def test_factory_rejects_raw_wp3_source_cells_mapping_and_duck_types():
    raw_wp3 = _channels(verbal=False)[0]
    raw_wp5 = _source()

    class DuckEligible:
        gate_status = "wp5_persistence_eligible"

    for candidate in (raw_wp3, raw_wp5, raw_wp5.__class__, {}, DuckEligible()):
        with pytest.raises(
            TypeError,
            match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT",
        ):
            create_public_metric(candidate)


def test_pydantic_type_adapter_cannot_bypass_either_persistence_gate():
    from pydantic import BaseModel

    if not hasattr(BaseModel, "model_validate"):
        pytest.skip("Pydantic 2 TypeAdapter surface")
    from pydantic import TypeAdapter

    eligible = minimize_public_metric_source(_source())
    raw_payload = eligible.model_dump()
    raw_payload["approved_statement"] = SENTINEL

    for model_type in (PersistenceEligibleMetricInput, PublicMetric):
        with pytest.raises(TypeError) as exc_info:
            TypeAdapter(model_type).validate_python(raw_payload)
        assert SENTINEL not in str(exc_info.value)


def test_safe_public_metric_pure_copy_is_allowed_but_structural_copy_stays_closed():
    metric = create_public_metric(minimize_public_metric_source(_source()))

    assert metric.model_copy() == metric
    assert metric.model_copy() is not metric
    assert metric.model_copy(deep=True) == metric
    assert metric.copy() == metric
    assert metric.copy(deep=True) == metric
    with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
        metric.model_copy(update={"approved_statement": SENTINEL})
    with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
        metric.copy(update={"approved_statement": SENTINEL})
    with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
        metric.copy(include={"metric_id"})
