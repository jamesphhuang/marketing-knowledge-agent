"""Early, in-memory minimization for Google Public Metric source cells."""

from __future__ import annotations

import hashlib
from datetime import date
from enum import Enum
from typing import Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, StrictBool, StrictStr

from .canonical_models import (
    CanonicalSourceLineage,
    ExposureChannel,
    LifecycleStatus,
    MetricId,
    PublishEligibility,
    ReviewStatus,
)
from .cell_normalization import FieldValueKind, InheritanceReason, ResolvedCellValue

_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")
if _PYDANTIC_V2:
    from pydantic import ConfigDict, field_validator
else:  # pragma: no cover - exercised only with Pydantic 1.x
    from pydantic import validator


_ALL_SOURCE_CHANNELS = (
    ExposureChannel.PRESS_RELEASE,
    ExposureChannel.OWNED_MEDIA,
    ExposureChannel.SALESKITS,
    ExposureChannel.VERBAL_BRIEFING,
    ExposureChannel.SPEAKING_DECK,
    ExposureChannel.WEBSITE_RECRUITING,
    ExposureChannel.ADS,
)
_SOURCE_CHANNEL_COLUMN_INDEXES = {
    channel: column_index
    for column_index, channel in enumerate(_ALL_SOURCE_CHANNELS, start=6)
}
_WRITTEN_CHANNELS = frozenset(
    channel
    for channel in _ALL_SOURCE_CHANNELS
    if channel is not ExposureChannel.VERBAL_BRIEFING
)
# User-approved WP5 implementation policy. Keep this exact set; it is not a
# frozen literal rule outside this work package.
_WRITTEN_RETENTION_BLOCKING_NOTE_MARKERS = (
    "不留文字紀錄",
    "不可書面",
    "禁止書面",
    "僅用於口頭",
    "只用於口頭",
    "不可留書面",
    "只能口頭",
    "不要留文字",
)
_PERSISTENCE_ELIGIBLE_STATUS = "wp5_persistence_eligible"
_DIGEST_DOMAIN = b"marketing-knowledge-agent:wp5:excluded-source:v1\0"


class MetricMinimizationError(ValueError):
    """Stable, source-payload-free WP5 failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ExclusionReason(str, Enum):
    ORAL_ONLY = "oral_only"


class _ImmutableDTO(BaseModel):
    if _PYDANTIC_V2:
        model_config = ConfigDict(
            arbitrary_types_allowed=True,
            extra="forbid",
            frozen=True,
            hide_input_in_errors=True,
        )
    else:  # pragma: no cover - exercised only with Pydantic 1.x

        class Config:
            allow_mutation = False
            arbitrary_types_allowed = True
            extra = "forbid"


class ExcludedSourceRef(_ImmutableDTO):
    """Persistable safe reference after irreversible payload exclusion."""

    sheet_id: int = Field(..., ge=0)
    source_row: int = Field(..., gt=0)
    metric_id: Optional[MetricId] = None
    reason: ExclusionReason
    source_digest: StrictStr = Field(..., min_length=1)

    if _PYDANTIC_V2:

        @field_validator("source_digest")
        @classmethod
        def validate_source_digest(cls, value):
            return _require_sha256_digest(value)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("source_digest")
        def validate_source_digest(cls, value):
            return _require_sha256_digest(value)


class MetricSourceCells:
    """Sensitive transient source values with no supported serialization surface."""

    __slots__ = (
        "_metric_id",
        "_metric_type",
        "_indicator",
        "_approved_statement",
        "_note",
        "_maintenance_updated_at",
        "_evidence_urls",
        "_channel_cells",
        "_lifecycle_status",
        "_review_status",
        "_publish_eligibility",
        "_can_quote_externally",
        "_source_lineage",
    )

    def __init__(
        self,
        *,
        metric_id,
        metric_type,
        indicator,
        approved_statement,
        note,
        maintenance_updated_at,
        evidence_urls,
        channel_cells,
        lifecycle_status,
        review_status,
        publish_eligibility,
        can_quote_externally,
        source_lineage,
    ):
        self._metric_id = metric_id
        self._metric_type = metric_type
        self._indicator = indicator
        self._approved_statement = approved_statement
        self._note = note
        self._maintenance_updated_at = maintenance_updated_at
        self._evidence_urls = tuple(evidence_urls)
        self._channel_cells = tuple(channel_cells)
        self._lifecycle_status = lifecycle_status
        self._review_status = review_status
        self._publish_eligibility = publish_eligibility
        self._can_quote_externally = can_quote_externally
        self._source_lineage = source_lineage

    def __repr__(self) -> str:
        if type(self._source_lineage) is CanonicalSourceLineage:
            return (
                "MetricSourceCells("
                f"sheet_id={self._source_lineage.sheet_id}, "
                f"source_row={self._source_lineage.source_row}, "
                "content=<redacted>)"
            )
        return "MetricSourceCells(source=<invalid>, content=<redacted>)"

    __str__ = __repr__

    def __getstate__(self):
        raise _metric_source_serialization_error()

    def __reduce__(self):
        raise _metric_source_serialization_error()

    def __reduce_ex__(self, protocol):
        raise _metric_source_serialization_error()


def _build_persistence_eligible_functions():
    authorization = object()

    def persistence_eligible_init(self, _wp5_gate=None, **data):
        if _wp5_gate is not authorization:
            raise _persistence_eligible_gate_error()
        _ImmutableDTO.__init__(self, **data)

    def minimize_public_metric_source(
        source: MetricSourceCells,
    ) -> MetricMinimizationResult:
        """Irreversibly exclude oral-only payloads before canonical persistence."""

        if type(source) is not MetricSourceCells:
            raise MetricMinimizationError("METRIC_SOURCE_CELLS_TYPE_INVALID")

        note_requires_exclusion = _note_requires_written_retention_exclusion(
            source._note
        )
        if note_requires_exclusion:
            return _excluded_source_ref(source)

        channel_values = _validated_channel_values(
            source._channel_cells,
            source._source_lineage,
        )
        oral_only = (
            channel_values[ExposureChannel.VERBAL_BRIEFING]
            and not any(channel_values[channel] for channel in _WRITTEN_CHANNELS)
        )

        if oral_only:
            return _excluded_source_ref(source)

        _validate_eligible_source(source, channel_values)
        allowed_channels = tuple(
            channel for channel in _ALL_SOURCE_CHANNELS if channel_values[channel]
        )
        return PersistenceEligibleMetricInput(
            _wp5_gate=authorization,
            metric_id=source._metric_id,
            metric_type=source._metric_type,
            indicator=source._indicator,
            approved_statement=source._approved_statement,
            maintenance_updated_at=source._maintenance_updated_at,
            evidence_urls=source._evidence_urls,
            allowed_exposure_channels=allowed_channels,
            lifecycle_status=source._lifecycle_status,
            review_status=source._review_status,
            publish_eligibility=source._publish_eligibility,
            can_quote_externally=source._can_quote_externally,
            source_lineage=source._source_lineage,
        )

    persistence_eligible_init.__name__ = "__init__"
    persistence_eligible_init.__qualname__ = "PersistenceEligibleMetricInput.__init__"
    minimize_public_metric_source.__qualname__ = "minimize_public_metric_source"
    return persistence_eligible_init, minimize_public_metric_source


_persistence_eligible_init, minimize_public_metric_source = (
    _build_persistence_eligible_functions()
)
del _build_persistence_eligible_functions


class PersistenceEligibleMetricInput(_ImmutableDTO):
    """Validated, immutable output that proves the WP5 gate was passed."""

    gate_status: Literal["wp5_persistence_eligible"] = _PERSISTENCE_ELIGIBLE_STATUS
    metric_id: MetricId
    metric_type: StrictStr = Field(..., min_length=1)
    indicator: StrictStr = Field(..., min_length=1)
    approved_statement: StrictStr = Field(..., min_length=1)
    maintenance_updated_at: Optional[date] = None
    evidence_urls: Tuple[StrictStr, ...] = ()
    allowed_exposure_channels: Tuple[ExposureChannel, ...] = ()
    lifecycle_status: LifecycleStatus
    review_status: ReviewStatus
    publish_eligibility: PublishEligibility
    can_quote_externally: StrictBool
    source_lineage: CanonicalSourceLineage

    if _PYDANTIC_V2:

        @field_validator("metric_type", "indicator", "approved_statement")
        @classmethod
        def require_non_blank_payload_text(cls, value):
            return _require_non_blank_text(value)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("metric_type", "indicator", "approved_statement")
        def require_non_blank_payload_text(cls, value):
            return _require_non_blank_text(value)

    __init__ = _persistence_eligible_init

    @classmethod
    def model_validate(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def model_validate_json(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def model_validate_strings(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def model_construct(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def parse_obj(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def parse_raw(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def parse_file(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def from_orm(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def construct(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    @classmethod
    def validate(cls, *args, **kwargs):
        raise _persistence_eligible_gate_error()

    if _PYDANTIC_V2:

        def model_copy(self, *, update=None, deep=False):
            _reject_eligible_copy_changes(update=update)
            return super().model_copy(update=update, deep=deep)

    def copy(self, *, include=None, exclude=None, update=None, deep=False):
        _reject_eligible_copy_changes(
            update=update,
            include=include,
            exclude=exclude,
        )
        return super().copy(deep=deep)


del _persistence_eligible_init

MetricMinimizationResult = Union[
    PersistenceEligibleMetricInput,
    ExcludedSourceRef,
]


def _validated_channel_values(
    channel_cells: Tuple[object, ...],
    source_lineage: object,
) -> dict:
    lineage = _safe_lineage(source_lineage)
    values = {}
    for cell in channel_cells:
        if type(cell) is not ResolvedCellValue:
            raise MetricMinimizationError("METRIC_CHANNEL_GOVERNANCE_UNCERTAIN")
        try:
            channel = ExposureChannel(cell.field_contract.field_name)
        except (TypeError, ValueError):
            raise MetricMinimizationError("METRIC_CHANNEL_SET_INVALID") from None
        if channel in values:
            raise MetricMinimizationError("METRIC_CHANNEL_SET_INVALID")
        validation = (
            cell.value_cell.data_validation
            if cell.value_cell is not None
            else None
        )
        if (
            cell.field_contract.value_kind is not FieldValueKind.BOOLEAN
            or type(cell.normalized_value) is not bool
            or validation is None
            or validation.condition.condition_type != "BOOLEAN"
        ):
            raise MetricMinimizationError("METRIC_CHANNEL_GOVERNANCE_UNCERTAIN")
        expected_row_index = lineage.source_row - 1
        expected_column_index = _SOURCE_CHANNEL_COLUMN_INDEXES[channel]
        if (
            cell.field_contract.source_column_index
            != expected_column_index
            or cell.lineage.sheet_id != lineage.sheet_id
            or cell.lineage.sheet_title != lineage.sheet_title
            or cell.lineage.source_row_index != expected_row_index
            or cell.lineage.source_column_index != expected_column_index
            or cell.source_cell.row_index != expected_row_index
            or cell.source_cell.column_index != expected_column_index
            or cell.value_cell.row_index != expected_row_index
            or cell.value_cell.column_index != expected_column_index
            or cell.field_lineage.field_name != channel.value
            or cell.field_lineage.target_row_index != expected_row_index
            or cell.field_lineage.target_column_index != expected_column_index
            or cell.field_lineage.value_row_index != expected_row_index
            or cell.field_lineage.value_column_index != expected_column_index
            or cell.field_lineage.inherited_from_merge
            or cell.field_lineage.inheritance_reason is not InheritanceReason.LOCAL
            or cell.field_lineage.merge_anchor_row_index is not None
            or cell.field_lineage.merge_anchor_column_index is not None
            or cell.field_lineage.merge_range is not None
        ):
            raise MetricMinimizationError("METRIC_CHANNEL_LINEAGE_INVALID")
        values[channel] = cell.normalized_value

    if set(values) != set(_ALL_SOURCE_CHANNELS):
        raise MetricMinimizationError("METRIC_CHANNEL_SET_INVALID")
    return values


def _excluded_source_ref(source: MetricSourceCells) -> ExcludedSourceRef:
    lineage = _safe_lineage(source._source_lineage)
    return ExcludedSourceRef(
        sheet_id=lineage.sheet_id,
        source_row=lineage.source_row,
        metric_id=(source._metric_id if type(source._metric_id) is MetricId else None),
        reason=ExclusionReason.ORAL_ONLY,
        source_digest=_compute_excluded_source_digest(source),
    )


def _note_requires_written_retention_exclusion(note: object) -> bool:
    if note is None:
        return False
    if type(note) is not str:
        raise MetricMinimizationError("METRIC_NOTE_TYPE_INVALID")
    normalized = note.casefold()
    return any(marker in normalized for marker in _WRITTEN_RETENTION_BLOCKING_NOTE_MARKERS)


def _validate_eligible_source(source: MetricSourceCells, channel_values: dict) -> None:
    if type(source._metric_id) is not MetricId:
        raise MetricMinimizationError("METRIC_ID_REQUIRED_FOR_PERSISTENCE")
    if any(
        type(value) is not str or not value.strip()
        for value in (
            source._metric_type,
            source._indicator,
            source._approved_statement,
        )
    ):
        raise MetricMinimizationError("METRIC_SOURCE_PAYLOAD_INVALID")
    if source._maintenance_updated_at is not None and type(
        source._maintenance_updated_at
    ) is not date:
        raise MetricMinimizationError("METRIC_MAINTENANCE_DATE_INVALID")
    if any(type(url) is not str or not url.strip() for url in source._evidence_urls):
        raise MetricMinimizationError("METRIC_EVIDENCE_REFERENCE_INVALID")
    if type(source._lifecycle_status) is not LifecycleStatus:
        raise MetricMinimizationError("METRIC_LIFECYCLE_STATUS_INVALID")
    if type(source._review_status) is not ReviewStatus:
        raise MetricMinimizationError("METRIC_REVIEW_STATUS_INVALID")
    if type(source._publish_eligibility) is not PublishEligibility:
        raise MetricMinimizationError("METRIC_PUBLISH_ELIGIBILITY_INVALID")
    if type(source._can_quote_externally) is not bool:
        raise MetricMinimizationError("METRIC_QUOTE_POLICY_UNCERTAIN")
    _safe_lineage(source._source_lineage)
    if source._can_quote_externally and not any(
        channel_values[channel] for channel in _WRITTEN_CHANNELS
    ):
        raise MetricMinimizationError("METRIC_QUOTE_POLICY_UNCERTAIN")


def _safe_lineage(lineage: object) -> CanonicalSourceLineage:
    if type(lineage) is not CanonicalSourceLineage:
        raise MetricMinimizationError("METRIC_SOURCE_LINEAGE_INVALID")
    return lineage


def _compute_excluded_source_digest(source: MetricSourceCells) -> str:
    """Hash a domain-separated, length-delimited sensitive source payload."""

    digest = hashlib.sha256()
    digest.update(_DIGEST_DOMAIN)
    values = (
        str(source._metric_id) if type(source._metric_id) is MetricId else "",
        source._metric_type if type(source._metric_type) is str else "",
        source._indicator if type(source._indicator) is str else "",
        source._approved_statement if type(source._approved_statement) is str else "",
        source._note if type(source._note) is str else "",
        *(url for url in source._evidence_urls if type(url) is str),
    )
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def _require_non_blank_text(value: str) -> str:
    if not value.strip():
        raise MetricMinimizationError("METRIC_SOURCE_PAYLOAD_INVALID")
    return value


def _require_sha256_digest(value: str) -> str:
    prefix = "sha256:"
    raw_digest = value[len(prefix) :] if value.startswith(prefix) else ""
    if len(raw_digest) != 64 or any(character not in "0123456789abcdef" for character in raw_digest):
        raise MetricMinimizationError("EXCLUDED_SOURCE_DIGEST_INVALID")
    return value


def _metric_source_serialization_error() -> TypeError:
    return TypeError("METRIC_SOURCE_CELLS_SERIALIZATION_FORBIDDEN")


def _persistence_eligible_gate_error() -> TypeError:
    return TypeError("PERSISTENCE_ELIGIBLE_INPUT_REQUIRES_WP5_GATE")


def _reject_eligible_copy_changes(*, update=None, include=None, exclude=None) -> None:
    if update or include is not None or exclude is not None:
        raise _persistence_eligible_gate_error()


__all__ = [
    "ExcludedSourceRef",
    "ExclusionReason",
    "MetricMinimizationError",
    "MetricMinimizationResult",
    "MetricSourceCells",
    "PersistenceEligibleMetricInput",
    "minimize_public_metric_source",
]
