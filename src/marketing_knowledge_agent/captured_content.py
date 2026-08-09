"""Canonical captured-content DTOs with no fetch or persistence side effects."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional, Tuple
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, StrictBool, StrictStr, conint

from .canonical_models import CanonicalSourceLineage, ContentAssetKey, MetricId
from .url_safety import CanonicalURL


_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")
if _PYDANTIC_V2:
    from pydantic import ConfigDict, field_validator, model_validator
    from pydantic_core import core_schema
else:  # pragma: no cover - exercised only with Pydantic 1.x
    from pydantic import root_validator, validator


class CapturedContentError(ValueError):
    """Stable, payload-free failure at the WP9 contract boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _OpaqueStableIdentifier(str):
    """Caller-supplied identity with no allocator or format namespace."""

    __slots__ = ()
    _invalid_code: ClassVar[str]
    _text_required_code: ClassVar[str]

    def __new__(cls, value: str):
        if cls is _OpaqueStableIdentifier:
            raise TypeError("OPAQUE_IDENTIFIER_CONCRETE_TYPE_REQUIRED")
        if type(value) is cls:
            return value
        if type(value) is not str:
            raise TypeError(cls._text_required_code)
        if (
            not value
            or not value.strip()
            or value != value.strip()
            or _contains_ascii_control(value)
        ):
            raise CapturedContentError(cls._invalid_code)
        return str.__new__(cls, value)

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, value):
        return cls(value)

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type="string", minLength=1)

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
            json_schema.update(minLength=1)
            return json_schema


class CapturedContentId(_OpaqueStableIdentifier):
    __slots__ = ()
    _invalid_code = "CAPTURED_CONTENT_ID_INVALID"
    _text_required_code = "CAPTURED_CONTENT_ID_TEXT_REQUIRED"


class EvidenceRelationshipId(_OpaqueStableIdentifier):
    __slots__ = ()
    _invalid_code = "EVIDENCE_RELATIONSHIP_ID_INVALID"
    _text_required_code = "EVIDENCE_RELATIONSHIP_ID_TEXT_REQUIRED"


class AuthorityRole(str, Enum):
    PRIMARY_CONTENT = "primary_content"
    EVIDENCE = "evidence"


class CaptureStatus(str, Enum):
    SUCCESS = "success"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    METADATA_ONLY = "metadata_only"
    NEEDS_REVIEW = "needs_review"


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

    if _PYDANTIC_V2:

        def model_copy(self, *, update=None, deep=False):
            _reject_unvalidated_copy(update=update)
            return super().model_copy(update=update, deep=deep)

    def copy(self, *, include=None, exclude=None, update=None, deep=False):
        _reject_unvalidated_copy(
            update=update,
            include=include,
            exclude=exclude,
        )
        return super().copy(
            include=include,
            exclude=exclude,
            update=update,
            deep=deep,
        )


_HttpStatusCode = conint(strict=True, ge=100, le=599)


class SafeHttpMetadata(_ImmutableDTO):
    """Exact allowlist of safe, opaque HTTP response metadata for WP9 v1.

    ``content_type`` is the sanitized response metadata value. It is distinct
    from ``CapturedContent.content_type`` and is not MIME-normalized by WP9.
    """

    status_code: Optional[_HttpStatusCode] = None
    content_type: Optional[StrictStr] = None
    etag: Optional[StrictStr] = None
    last_modified: Optional[StrictStr] = None
    verified_final_url: Optional[CanonicalURL] = None

    if _PYDANTIC_V2:

        @field_validator("content_type", "etag", "last_modified")
        @classmethod
        def validate_safe_http_text(cls, value):
            return _optional_safe_http_text(value)

        @field_validator("verified_final_url")
        @classmethod
        def validate_verified_final_url(cls, value):
            return _optional_canonical_url(value)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("content_type", "etag", "last_modified")
        def validate_safe_http_text(cls, value):
            return _optional_safe_http_text(value)

        @validator("verified_final_url")
        def validate_verified_final_url(cls, value):
            return _optional_canonical_url(value)


class Section(_ImmutableDTO):
    """Minimal normalized section shape; tuple position carries order."""

    heading: Optional[StrictStr] = None
    text: StrictStr

    if _PYDANTIC_V2:

        @field_validator("heading")
        @classmethod
        def validate_heading(cls, value):
            return _optional_section_heading(value)

        @field_validator("text")
        @classmethod
        def validate_text(cls, value):
            return _section_text(value)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("heading")
        def validate_heading(cls, value):
            return _optional_section_heading(value)

        @validator("text")
        def validate_text(cls, value):
            return _section_text(value)

    def __repr__(self) -> str:
        return f"Section(heading={self.heading!r}, text=<redacted>)"

    __str__ = __repr__


class CapturedContent(_ImmutableDTO):
    """One canonical logical capture state under an exact authority parent.

    Top-level ``content_type`` is a caller-supplied canonical capture
    classification. It may differ from opaque HTTP response metadata.
    """

    _identity_fields: ClassVar[Tuple[str, ...]] = ("captured_content_id",)

    captured_content_id: CapturedContentId
    asset_key: Optional[ContentAssetKey] = None
    metric_id: Optional[MetricId] = None
    evidence_relationship_id: Optional[EvidenceRelationshipId] = None
    authority_role: AuthorityRole
    source_url: CanonicalURL
    canonical_url: CanonicalURL
    source_domain: StrictStr = Field(..., min_length=1)
    content_type: Optional[StrictStr] = None
    title: Optional[StrictStr] = None
    clean_body: Optional[StrictStr] = None
    section_structure: Tuple[Section, ...] = ()
    capture_status: CaptureStatus
    captured_at: Optional[datetime] = None
    last_successful_capture_at: Optional[datetime] = None
    last_capture_attempt_at: Optional[datetime] = None
    content_hash: Optional[StrictStr] = None
    parser_version: Optional[StrictStr] = None
    source_http_metadata: SafeHttpMetadata = Field(default_factory=SafeHttpMetadata)
    previous_content_hash: Optional[StrictStr] = None
    searchable: StrictBool
    source_lineage: CanonicalSourceLineage
    sync_batch_id: StrictStr = Field(..., min_length=1)

    if _PYDANTIC_V2:

        @field_validator("section_structure", mode="before")
        @classmethod
        def require_section_tuple(cls, value):
            return _section_tuple(value)

        @field_validator(
            "source_domain",
            "content_type",
            "title",
            "clean_body",
            "content_hash",
            "parser_version",
            "previous_content_hash",
            "sync_batch_id",
        )
        @classmethod
        def require_non_blank_present_text(cls, value):
            return _optional_non_blank_text(value)

        @field_validator(
            "captured_at",
            "last_successful_capture_at",
            "last_capture_attempt_at",
            mode="before",
        )
        @classmethod
        def require_datetime_objects(cls, value):
            return _datetime_object(value)

        @field_validator(
            "captured_at",
            "last_successful_capture_at",
            "last_capture_attempt_at",
        )
        @classmethod
        def require_aware_timestamps(cls, value):
            return _aware_datetime(value)

        @field_validator("source_url", "canonical_url")
        @classmethod
        def require_canonical_urls(cls, value):
            return _required_canonical_url(value)

        @model_validator(mode="after")
        def validate_contract(self):
            _validate_captured_content(self)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("section_structure", pre=True)
        def require_section_tuple(cls, value):
            return _section_tuple(value)

        @validator(
            "source_domain",
            "content_type",
            "title",
            "clean_body",
            "content_hash",
            "parser_version",
            "previous_content_hash",
            "sync_batch_id",
        )
        def require_non_blank_present_text(cls, value):
            return _optional_non_blank_text(value)

        @validator(
            "captured_at",
            "last_successful_capture_at",
            "last_capture_attempt_at",
            pre=True,
        )
        def require_datetime_objects(cls, value):
            return _datetime_object(value)

        @validator(
            "captured_at",
            "last_successful_capture_at",
            "last_capture_attempt_at",
        )
        def require_aware_timestamps(cls, value):
            return _aware_datetime(value)

        @validator("source_url", "canonical_url")
        def require_canonical_urls(cls, value):
            return _required_canonical_url(value)

        @root_validator(skip_on_failure=True)
        def validate_contract(cls, values):
            _validate_captured_content_values(values)
            return values

    @classmethod
    def identity_field_names(cls) -> Tuple[str, ...]:
        return cls._identity_fields

    def __repr__(self) -> str:
        parent = (
            f"asset_key={str(self.asset_key)!r}"
            if self.asset_key is not None
            else (
                f"metric_id={str(self.metric_id)!r}, "
                f"evidence_relationship_id={str(self.evidence_relationship_id)!r}"
            )
        )
        return (
            "CapturedContent("
            f"captured_content_id={str(self.captured_content_id)!r}, "
            f"authority_role={self.authority_role.value!r}, "
            f"{parent}, "
            f"capture_status={self.capture_status.value!r}, "
            f"searchable={self.searchable!r}, "
            f"section_count={len(self.section_structure)}, "
            "clean_body=<redacted>)"
        )

    __str__ = __repr__


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _contains_unsafe_section_control(value: str) -> bool:
    return any(
        (ord(character) < 32 and character != "\n") or ord(character) == 127
        for character in value
    )


def _optional_safe_http_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not value.strip() or _contains_ascii_control(value):
        raise CapturedContentError("SAFE_HTTP_METADATA_TEXT_INVALID")
    return value


def _optional_canonical_url(value: Optional[CanonicalURL]) -> Optional[CanonicalURL]:
    if value is not None and type(value) is not CanonicalURL:
        raise CapturedContentError("SAFE_HTTP_METADATA_FINAL_URL_INVALID")
    return value


def _required_canonical_url(value: CanonicalURL) -> CanonicalURL:
    if type(value) is not CanonicalURL:
        raise CapturedContentError("CAPTURED_CONTENT_CANONICAL_URL_REQUIRED")
    return value


def _optional_section_heading(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not value.strip() or _contains_ascii_control(value):
        raise CapturedContentError("SECTION_HEADING_INVALID")
    return value


def _section_text(value: str) -> str:
    if not value.strip() or _contains_unsafe_section_control(value):
        raise CapturedContentError("SECTION_TEXT_INVALID")
    return value


def _section_tuple(value) -> Tuple[Section, ...]:
    if type(value) is not tuple:
        raise CapturedContentError("CAPTURED_CONTENT_SECTION_TUPLE_REQUIRED")
    return value


def _optional_non_blank_text(value: Optional[str]) -> Optional[str]:
    if value is not None and not value.strip():
        raise CapturedContentError("CAPTURED_CONTENT_TEXT_INVALID")
    return value


def _datetime_object(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and type(value) is not datetime:
        raise CapturedContentError("CAPTURED_CONTENT_TIMESTAMP_TYPE_INVALID")
    return value


def _aware_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.utcoffset() is None:
        raise CapturedContentError("CAPTURED_CONTENT_TIMESTAMP_AWARE_REQUIRED")
    return value


def _validate_captured_content(record: CapturedContent) -> None:
    _validate_captured_content_values(record.__dict__)


def _validate_captured_content_values(values) -> None:
    authority_role = values.get("authority_role")
    asset_key = values.get("asset_key")
    metric_id = values.get("metric_id")
    relationship_id = values.get("evidence_relationship_id")

    if authority_role is AuthorityRole.PRIMARY_CONTENT:
        if (
            type(asset_key) is not ContentAssetKey
            or metric_id is not None
            or relationship_id is not None
        ):
            raise CapturedContentError("CAPTURED_CONTENT_PRIMARY_PARENT_INVALID")
    elif authority_role is AuthorityRole.EVIDENCE:
        if (
            asset_key is not None
            or type(metric_id) is not MetricId
            or type(relationship_id) is not EvidenceRelationshipId
        ):
            raise CapturedContentError("CAPTURED_CONTENT_EVIDENCE_PARENT_INVALID")
    else:
        raise CapturedContentError("CAPTURED_CONTENT_AUTHORITY_INVALID")

    source_lineage = values.get("source_lineage")
    if type(source_lineage) is not CanonicalSourceLineage:
        raise CapturedContentError("CAPTURED_CONTENT_SOURCE_LINEAGE_INVALID")
    if values.get("sync_batch_id") != source_lineage.sync_batch_id:
        raise CapturedContentError("CAPTURED_CONTENT_SYNC_BATCH_MISMATCH")

    _validate_url_metadata_consistency(values)
    _validate_status_body_matrix(values)
    _validate_timestamp_matrix(values)


def _validate_url_metadata_consistency(values) -> None:
    source_url = values.get("source_url")
    canonical_url = values.get("canonical_url")
    source_domain = values.get("source_domain")
    http_metadata = values.get("source_http_metadata")

    if source_domain != _trusted_canonical_hostname(source_url):
        raise CapturedContentError("CAPTURED_CONTENT_SOURCE_DOMAIN_MISMATCH")

    verified_final_url = http_metadata.verified_final_url
    if verified_final_url is not None and verified_final_url != canonical_url:
        raise CapturedContentError("CAPTURED_CONTENT_FINAL_URL_MISMATCH")


def _trusted_canonical_hostname(url: CanonicalURL) -> str:
    try:
        hostname = urlsplit(url.value).hostname
    except (UnicodeError, ValueError) as error:  # trusted type invariant
        raise CapturedContentError(
            "CAPTURED_CONTENT_SOURCE_DOMAIN_MISMATCH"
        ) from error
    if hostname is None:  # trusted type invariant
        raise CapturedContentError("CAPTURED_CONTENT_SOURCE_DOMAIN_MISMATCH")
    return hostname


def _validate_status_body_matrix(values) -> None:
    status = values.get("capture_status")
    full_text_status = status in (CaptureStatus.SUCCESS, CaptureStatus.STALE)
    if full_text_status:
        required_values = (
            values.get("clean_body"),
            values.get("content_hash"),
            values.get("parser_version"),
            values.get("captured_at"),
            values.get("last_successful_capture_at"),
            values.get("last_capture_attempt_at"),
        )
        if any(value is None for value in required_values):
            raise CapturedContentError("CAPTURED_CONTENT_STATUS_BODY_INVALID")
        return

    if status not in (
        CaptureStatus.UNAVAILABLE,
        CaptureStatus.BLOCKED,
        CaptureStatus.METADATA_ONLY,
        CaptureStatus.NEEDS_REVIEW,
    ):
        raise CapturedContentError("CAPTURED_CONTENT_STATUS_INVALID")
    if any(
        (
            values.get("clean_body") is not None,
            values.get("section_structure") != (),
            values.get("content_hash") is not None,
            values.get("parser_version") is not None,
            values.get("captured_at") is not None,
            values.get("searchable") is not False,
        )
    ):
        raise CapturedContentError("CAPTURED_CONTENT_STATUS_BODY_INVALID")


def _validate_timestamp_matrix(values) -> None:
    status = values.get("capture_status")
    captured_at = values.get("captured_at")
    last_success = values.get("last_successful_capture_at")
    last_attempt = values.get("last_capture_attempt_at")

    if status in (CaptureStatus.SUCCESS, CaptureStatus.STALE):
        if not captured_at <= last_success <= last_attempt:
            raise CapturedContentError("CAPTURED_CONTENT_TIMESTAMP_INVALID")
        return
    if status is CaptureStatus.UNAVAILABLE and last_attempt is None:
        raise CapturedContentError("CAPTURED_CONTENT_TIMESTAMP_INVALID")
    if last_success is not None and last_attempt is not None:
        if last_success > last_attempt:
            raise CapturedContentError("CAPTURED_CONTENT_TIMESTAMP_INVALID")


def _reject_unvalidated_copy(*, update=None, include=None, exclude=None) -> None:
    if update or include is not None or exclude is not None:
        raise CapturedContentError("WP9_UNVALIDATED_COPY_NOT_ALLOWED")


__all__ = [
    "AuthorityRole",
    "CapturedContent",
    "CapturedContentError",
    "CapturedContentId",
    "CaptureStatus",
    "EvidenceRelationshipId",
    "SafeHttpMetadata",
    "Section",
]
