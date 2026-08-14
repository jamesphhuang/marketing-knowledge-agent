"""Canonical identity and entity contracts with no source or persistence side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Dict, Iterable, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, Field, StrictBool, StrictStr

_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")
if _PYDANTIC_V2:
    from pydantic import ConfigDict, field_validator, model_validator
    from pydantic_core import core_schema
else:  # pragma: no cover - exercised only with Pydantic 1.x
    from pydantic import root_validator, validator

if TYPE_CHECKING:
    from .google_normalization import PersistenceEligibleMetricInput


class CanonicalModelError(ValueError):
    """Stable, payload-free canonical contract failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _PermanentId(str):
    """Exact, immutable permanent ID in one fixed namespace."""

    __slots__ = ()
    _prefix: ClassVar[str]

    def __new__(cls, value: str):
        if cls is _PermanentId or not isinstance(value, str):
            raise TypeError("PERMANENT_ID_TEXT_REQUIRED")
        pattern = rf"{re.escape(cls._prefix)}-[0-9]{{4,}}"
        if re.fullmatch(pattern, value) is None:
            raise CanonicalModelError("PERMANENT_ID_FORMAT_INVALID")
        return str.__new__(cls, value)

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, value):
        return cls(value)

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type="string", pattern=cls._json_schema_pattern())

    @classmethod
    def _json_schema_pattern(cls) -> str:
        return rf"^{re.escape(cls._prefix)}-[0-9]{{4,}}$"

    if _PYDANTIC_V2:

        @classmethod
        def __get_pydantic_core_schema__(cls, source_type, handler):
            return core_schema.no_info_after_validator_function(
                cls._validate,
                core_schema.str_schema(strict=True),
            )

        @classmethod
        def __get_pydantic_json_schema__(cls, schema, handler):
            json_schema = handler(schema)
            json_schema.update(pattern=cls._json_schema_pattern())
            return json_schema


class SourceRecordId(_PermanentId):
    __slots__ = ()
    _prefix = "MREC"


class BrandId(_PermanentId):
    __slots__ = ()
    _prefix = "BRD"


class MetricId(_PermanentId):
    __slots__ = ()
    _prefix = "MET"


class AssetType(str, Enum):
    ARTICLE = "article"
    VIDEO = "video"
    PODCAST = "podcast"
    NEWS = "news"


class BrandEntityType(str, Enum):
    MERCHANT = "merchant"
    PARTNER = "partner"
    OTHER = "other"


class LifecycleStatus(str, Enum):
    CANDIDATE = "candidate"
    NEEDS_REVIEW = "needs_review"
    ACTIVE = "active"
    INCOMPLETE = "incomplete"
    ARCHIVED = "archived"


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    EXCLUDED = "excluded"


class PublishEligibility(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


class ExposureChannel(str, Enum):
    PRESS_RELEASE = "press_release"
    OWNED_MEDIA = "owned_media"
    SALESKITS = "saleskits"
    VERBAL_BRIEFING = "verbal_briefing"
    SPEAKING_DECK = "speaking_deck"
    WEBSITE_RECRUITING = "website_recruiting"
    ADS = "ads"


@dataclass(frozen=True)
class ContentAssetKey:
    """The v1 asset identity: one asset type below one MREC."""

    source_record_id: SourceRecordId
    asset_type: AssetType

    def __post_init__(self) -> None:
        if type(self.source_record_id) is not SourceRecordId:
            raise TypeError("CONTENT_ASSET_KEY_SOURCE_NAMESPACE_MISMATCH")
        if not isinstance(self.asset_type, AssetType):
            raise TypeError("CONTENT_ASSET_KEY_ASSET_TYPE_INVALID")

    def __str__(self) -> str:
        return f"{self.source_record_id}:{self.asset_type.value}"

    @classmethod
    def parse(cls, value: str) -> "ContentAssetKey":
        if not isinstance(value, str):
            raise TypeError("CONTENT_ASSET_KEY_TEXT_REQUIRED")
        try:
            raw_source_record_id, raw_asset_type = value.split(":", 1)
            source_record_id = SourceRecordId(raw_source_record_id)
            asset_type = AssetType(raw_asset_type)
        except (CanonicalModelError, ValueError) as exc:
            raise CanonicalModelError("CONTENT_ASSET_KEY_FORMAT_INVALID") from exc
        return cls(source_record_id=source_record_id, asset_type=asset_type)


class _CanonicalModel(BaseModel):
    _identity_fields: ClassVar[Tuple[str, ...]] = ()

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

    @classmethod
    def identity_field_names(cls) -> Tuple[str, ...]:
        return cls._identity_fields


class CanonicalSourceLineage(_CanonicalModel):
    """Source location metadata that may change without changing identity."""

    spreadsheet_id_hash: StrictStr = Field(..., min_length=1)
    sheet_id: int = Field(..., ge=0)
    sheet_title: StrictStr = Field(..., min_length=1)
    source_row: int = Field(..., gt=0)
    source_columns: Dict[StrictStr, StrictStr] = Field(default_factory=dict)
    source_ranges: Dict[StrictStr, StrictStr] = Field(default_factory=dict)
    source_fingerprint: StrictStr = Field(..., min_length=1)
    sync_batch_id: StrictStr = Field(..., min_length=1)

    if _PYDANTIC_V2:

        @field_validator(
            "spreadsheet_id_hash",
            "sheet_title",
            "source_fingerprint",
            "sync_batch_id",
        )
        @classmethod
        def require_non_blank_text(cls, value):
            return _require_non_blank_text(value, "CANONICAL_LINEAGE_TEXT_BLANK")

        @field_validator("source_columns", "source_ranges")
        @classmethod
        def require_non_blank_mapping_entries(cls, value):
            return _require_non_blank_mapping_entries(value)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator(
            "spreadsheet_id_hash",
            "sheet_title",
            "source_fingerprint",
            "sync_batch_id",
        )
        def require_non_blank_text(cls, value):
            return _require_non_blank_text(value, "CANONICAL_LINEAGE_TEXT_BLANK")

        @validator("source_columns", "source_ranges")
        def require_non_blank_mapping_entries(cls, value):
            return _require_non_blank_mapping_entries(value)


class BrandIdentityDecision(_CanonicalModel):
    """Human-review result for a SourceRecord-to-Brand relationship."""

    review_status: ReviewStatus
    brand_id: Optional[BrandId] = None

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def validate_brand_assignment(self):
            _validate_brand_identity_decision(self.review_status, self.brand_id)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @root_validator(skip_on_failure=True)
        def validate_brand_assignment(cls, values):
            _validate_brand_identity_decision(
                values.get("review_status"),
                values.get("brand_id"),
            )
            return values


class Brand(_CanonicalModel):
    _identity_fields = ("brand_id",)

    brand_id: BrandId
    canonical_name: StrictStr = Field(..., min_length=1)
    entity_type: BrandEntityType
    handle: Optional[StrictStr] = None
    official_website: Optional[StrictStr] = None
    aliases: Tuple[StrictStr, ...] = ()
    lifecycle_status: LifecycleStatus
    review_status: ReviewStatus
    publish_eligibility: PublishEligibility
    source_lineage: CanonicalSourceLineage

    if _PYDANTIC_V2:

        @field_validator("canonical_name")
        @classmethod
        def require_non_blank_name(cls, value):
            return _require_non_blank_text(value, "BRAND_CANONICAL_NAME_BLANK")

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("canonical_name")
        def require_non_blank_name(cls, value):
            return _require_non_blank_text(value, "BRAND_CANONICAL_NAME_BLANK")


class SourceRecord(_CanonicalModel):
    _identity_fields = ("source_record_id",)

    source_record_id: SourceRecordId
    brand_identity: BrandIdentityDecision
    interview_year: Optional[int] = None
    source_name: StrictStr = Field(..., min_length=1)
    sales_category_lv1: Optional[StrictStr] = None
    sales_category_lv2: Optional[StrictStr] = None
    tags: Tuple[StrictStr, ...] = ()
    lifecycle_status: LifecycleStatus
    review_status: ReviewStatus
    publish_eligibility: PublishEligibility
    source_lineage: CanonicalSourceLineage

    if _PYDANTIC_V2:

        @field_validator("source_name")
        @classmethod
        def require_non_blank_source_name(cls, value):
            return _require_non_blank_text(value, "SOURCE_RECORD_NAME_BLANK")

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("source_name")
        def require_non_blank_source_name(cls, value):
            return _require_non_blank_text(value, "SOURCE_RECORD_NAME_BLANK")

    @property
    def brand_id(self) -> Optional[BrandId]:
        return self.brand_identity.brand_id


def _build_public_metric_functions():
    # Guard scope: docs/governance/PYTHON_GUARD_THREAT_MODEL.md (T1/T2, not T3).
    authorization = object()

    def public_metric_init(self, _wp5_gate=None, **data):
        if _wp5_gate is not authorization:
            raise _public_metric_gate_error()
        _validate_public_metric_governance_state(
            review_status=data.get("review_status"),
            publish_eligibility=data.get("publish_eligibility"),
            can_quote_externally=data.get("can_quote_externally"),
        )
        _CanonicalModel.__init__(self, **data)

    def create_public_metric(eligible: PersistenceEligibleMetricInput) -> PublicMetric:
        """Build a normally validated PublicMetric from the exact WP5 output type."""

        from .google_normalization import PersistenceEligibleMetricInput

        if type(eligible) is not PersistenceEligibleMetricInput:
            raise _public_metric_gate_error()
        return PublicMetric(
            _wp5_gate=authorization,
            metric_id=eligible.metric_id,
            metric_type=eligible.metric_type,
            indicator=eligible.indicator,
            approved_statement=eligible.approved_statement,
            maintenance_updated_at=eligible.maintenance_updated_at,
            evidence_urls=eligible.evidence_urls,
            allowed_exposure_channels=eligible.allowed_exposure_channels,
            lifecycle_status=eligible.lifecycle_status,
            review_status=eligible.review_status,
            publish_eligibility=eligible.publish_eligibility,
            can_quote_externally=eligible.can_quote_externally,
            source_lineage=eligible.source_lineage,
        )

    public_metric_init.__name__ = "__init__"
    public_metric_init.__qualname__ = "PublicMetric.__init__"
    create_public_metric.__qualname__ = "create_public_metric"
    return public_metric_init, create_public_metric


_public_metric_init, create_public_metric = _build_public_metric_functions()
del _build_public_metric_functions


class PublicMetric(_CanonicalModel):
    """Final MET schema; governance fields are declarative until enforced."""

    _identity_fields = ("metric_id",)

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
            return _require_non_blank_text(value, "PUBLIC_METRIC_TEXT_BLANK")

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("metric_type", "indicator", "approved_statement")
        def require_non_blank_payload_text(cls, value):
            return _require_non_blank_text(value, "PUBLIC_METRIC_TEXT_BLANK")

    __init__ = _public_metric_init

    @classmethod
    def model_validate(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def model_validate_json(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def model_validate_strings(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def model_construct(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def parse_obj(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def parse_raw(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def parse_file(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def from_orm(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def construct(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    @classmethod
    def validate(cls, *args, **kwargs):
        raise _public_metric_gate_error()

    if _PYDANTIC_V2:

        def model_copy(self, *, update=None, deep=False):
            _reject_public_metric_copy_changes(update=update)
            return super().model_copy(update=update, deep=deep)

    def copy(self, *, include=None, exclude=None, update=None, deep=False):
        _reject_public_metric_copy_changes(
            update=update,
            include=include,
            exclude=exclude,
        )
        return super().copy(deep=deep)


del _public_metric_init


_PermanentIdT = TypeVar("_PermanentIdT", bound=_PermanentId)


def validate_unique_source_record_ids(
    values: Iterable[SourceRecordId],
) -> Tuple[SourceRecordId, ...]:
    return _validate_unique_ids(
        values,
        expected_type=SourceRecordId,
        namespace_error="SOURCE_RECORD_ID_NAMESPACE_MISMATCH",
        duplicate_error="SOURCE_RECORD_ID_DUPLICATE",
    )


def validate_unique_brand_ids(values: Iterable[BrandId]) -> Tuple[BrandId, ...]:
    return _validate_unique_ids(
        values,
        expected_type=BrandId,
        namespace_error="BRAND_ID_NAMESPACE_MISMATCH",
        duplicate_error="BRAND_ID_DUPLICATE",
    )


def validate_unique_metric_ids(values: Iterable[MetricId]) -> Tuple[MetricId, ...]:
    return _validate_unique_ids(
        values,
        expected_type=MetricId,
        namespace_error="METRIC_ID_NAMESPACE_MISMATCH",
        duplicate_error="METRIC_ID_DUPLICATE",
    )


def _validate_unique_ids(
    values: Iterable[_PermanentIdT],
    *,
    expected_type: Type[_PermanentIdT],
    namespace_error: str,
    duplicate_error: str,
) -> Tuple[_PermanentIdT, ...]:
    result = tuple(values)
    if any(type(value) is not expected_type for value in result):
        raise TypeError(namespace_error)
    if len(set(result)) != len(result):
        raise CanonicalModelError(duplicate_error)
    return result


def _validate_brand_identity_decision(
    review_status: ReviewStatus,
    brand_id: Optional[BrandId],
) -> None:
    if review_status is ReviewStatus.APPROVED and brand_id is None:
        raise CanonicalModelError("BRAND_ID_REQUIRED_FOR_APPROVED_DECISION")
    if review_status is ReviewStatus.NEEDS_REVIEW and brand_id is not None:
        raise CanonicalModelError("BRAND_ID_FORBIDDEN_FOR_UNCERTAIN_DECISION")
    if review_status is ReviewStatus.EXCLUDED and brand_id is not None:
        raise CanonicalModelError("BRAND_ID_FORBIDDEN_FOR_EXCLUDED_DECISION")


def _validate_public_metric_governance_state(
    *,
    review_status: object,
    publish_eligibility: object,
    can_quote_externally: object,
) -> None:
    """Validate the shared PublicMetric quote/exclusion state invariant."""

    if can_quote_externally is True and (
        review_status is not ReviewStatus.APPROVED
        or publish_eligibility is not PublishEligibility.ELIGIBLE
    ):
        raise CanonicalModelError("PUBLIC_METRIC_GOVERNANCE_STATE_INVALID")
    if review_status is ReviewStatus.EXCLUDED and (
        can_quote_externally is not False
        or publish_eligibility is not PublishEligibility.INELIGIBLE
    ):
        raise CanonicalModelError("PUBLIC_METRIC_GOVERNANCE_STATE_INVALID")


def _require_non_blank_text(value: str, code: str) -> str:
    if not value.strip():
        raise CanonicalModelError(code)
    return value


def _require_non_blank_mapping_entries(value: Dict[str, str]) -> Dict[str, str]:
    if any(not key.strip() or not item.strip() for key, item in value.items()):
        raise CanonicalModelError("CANONICAL_LINEAGE_MAPPING_ENTRY_BLANK")
    return value


def _public_metric_gate_error() -> TypeError:
    return TypeError("PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT")


def _reject_public_metric_copy_changes(*, update=None, include=None, exclude=None) -> None:
    if update or include is not None or exclude is not None:
        raise _public_metric_gate_error()


__all__ = [
    "AssetType",
    "Brand",
    "BrandEntityType",
    "BrandId",
    "BrandIdentityDecision",
    "CanonicalModelError",
    "CanonicalSourceLineage",
    "ContentAssetKey",
    "ExposureChannel",
    "LifecycleStatus",
    "MetricId",
    "PublicMetric",
    "PublishEligibility",
    "ReviewStatus",
    "SourceRecord",
    "SourceRecordId",
    "create_public_metric",
    "validate_unique_brand_ids",
    "validate_unique_metric_ids",
    "validate_unique_source_record_ids",
]
