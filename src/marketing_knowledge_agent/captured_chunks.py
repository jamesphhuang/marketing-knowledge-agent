"""Pure captured-chunk metadata and revision-scoped identity contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from .canonical_models import (
    BrandId,
    CanonicalSourceLineage,
    ContentAssetKey,
    MetricId,
    SourceRecord,
    SourceRecordId,
)
from .captured_content import (
    AuthorityRole,
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
    EvidenceRelationshipId,
)
from .content_hashing import (
    CaptureContentHash,
    CaptureRevisionRef,
    ContentHashingError,
    LkgEligibilityInput,
    LkgEligibilityResult,
    compose_stale_lkg,
)
from .url_safety import CanonicalURL


_CHUNK_TEXT_DOMAIN = b"MKA_CAPTURED_CHUNK_TEXT_V1\x00"
_CHUNK_ID_DOMAIN = b"MKA_CAPTURED_CHUNK_ID_V1\x00"
_CHUNK_ID_PATTERN = re.compile(r"chk:v1:sha256:[0-9a-f]{64}")


class CapturedChunkError(ValueError):
    """Stable, payload-free failure at the WP13 contract boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SectionAnchor(str):
    """Caller-supplied opaque stable section identity."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if (
            type(value) is not str
            or not value
            or not value.strip()
            or value != value.strip()
            or _contains_ascii_control(value)
        ):
            raise CapturedChunkError("SECTION_ANCHOR_INVALID")
        return str.__new__(cls, value)


class CapturedChunkId(str):
    """Strict serialized revision-scoped chunk identity."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if type(value) is not str or _CHUNK_ID_PATTERN.fullmatch(value) is None:
            raise CapturedChunkError("CAPTURED_CHUNK_ID_INVALID")
        return str.__new__(cls, value)


@dataclass(frozen=True, repr=False)
class SyntheticChunkSpan:
    """One caller-injected span over ``CapturedContent.clean_body``."""

    text: str
    start: int
    end: int
    section_anchor: SectionAnchor
    section_heading: Optional[str]
    ordinal: int

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text.strip():
            raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_TEXT_INVALID")
        if type(self.start) is not int:
            raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_START_INVALID")
        if type(self.end) is not int:
            raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_END_INVALID")
        if self.start < 0 or self.end <= self.start:
            raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_RANGE_INVALID")
        if type(self.section_anchor) is not SectionAnchor:
            raise CapturedChunkError("SYNTHETIC_CHUNK_SECTION_ANCHOR_INVALID")
        _validate_section_heading(self.section_heading)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_ORDINAL_INVALID")

    def __repr__(self) -> str:
        return (
            "SyntheticChunkSpan("
            "text=<redacted>, "
            f"start={self.start!r}, "
            f"end={self.end!r}, "
            f"section_anchor={str(self.section_anchor)!r}, "
            "section_heading=<redacted>, "
            f"ordinal={self.ordinal!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class CapturedChunkSourceLineage:
    """Deterministic immutable snapshot of canonical source lineage."""

    spreadsheet_id_hash: str
    sheet_id: int
    sheet_title: str
    source_row: int
    source_columns: Tuple[Tuple[str, str], ...]
    source_ranges: Tuple[Tuple[str, str], ...]
    source_fingerprint: str
    sync_batch_id: str

    def __post_init__(self) -> None:
        _validate_lineage_text(
            self.spreadsheet_id_hash,
            "CAPTURED_CHUNK_LINEAGE_SPREADSHEET_HASH_INVALID",
        )
        if type(self.sheet_id) is not int or self.sheet_id < 0:
            raise CapturedChunkError("CAPTURED_CHUNK_LINEAGE_SHEET_ID_INVALID")
        _validate_lineage_text(
            self.sheet_title,
            "CAPTURED_CHUNK_LINEAGE_SHEET_TITLE_INVALID",
        )
        if type(self.source_row) is not int or self.source_row <= 0:
            raise CapturedChunkError("CAPTURED_CHUNK_LINEAGE_SOURCE_ROW_INVALID")
        _validate_lineage_pairs(
            self.source_columns,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
        )
        _validate_lineage_pairs(
            self.source_ranges,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
        )
        _validate_lineage_text(
            self.source_fingerprint,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_FINGERPRINT_INVALID",
        )
        _validate_lineage_text(
            self.sync_batch_id,
            "CAPTURED_CHUNK_LINEAGE_SYNC_BATCH_INVALID",
        )

    def __repr__(self) -> str:
        return (
            "CapturedChunkSourceLineage("
            f"sheet_id={self.sheet_id!r}, "
            f"source_row={self.source_row!r}, "
            "source_columns=<redacted>, "
            "source_ranges=<redacted>)"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class CapturedChunkMetadata:
    """Body-free authority, revision, source, and freshness metadata."""

    chunk_id: CapturedChunkId
    revision_ref: CaptureRevisionRef
    asset_key: Optional[ContentAssetKey]
    metric_id: Optional[MetricId]
    evidence_relationship_id: Optional[EvidenceRelationshipId]
    brand_id: Optional[BrandId]
    source_record_id: Optional[SourceRecordId]
    authority_role: AuthorityRole
    title: Optional[str]
    section_anchor: SectionAnchor
    section_heading: Optional[str]
    chunk_ordinal: int
    source_url: CanonicalURL
    capture_status: CaptureStatus
    captured_at: datetime
    last_successful_capture_at: datetime
    last_capture_attempt_at: datetime
    searchable: bool
    source_lineage: CapturedChunkSourceLineage

    def __post_init__(self) -> None:
        if type(self.chunk_id) is not CapturedChunkId:
            raise CapturedChunkError("CAPTURED_CHUNK_ID_TYPE_INVALID")
        if type(self.revision_ref) is not CaptureRevisionRef:
            raise CapturedChunkError("CAPTURED_CHUNK_REVISION_REF_INVALID")
        if type(self.authority_role) is not AuthorityRole:
            raise CapturedChunkError("CAPTURED_CHUNK_AUTHORITY_INVALID")
        _validate_metadata_authority(self)
        if self.title is not None and (
            type(self.title) is not str or not self.title.strip()
        ):
            raise CapturedChunkError("CAPTURED_CHUNK_TITLE_INVALID")
        if type(self.section_anchor) is not SectionAnchor:
            raise CapturedChunkError("CAPTURED_CHUNK_SECTION_ANCHOR_INVALID")
        _validate_section_heading(self.section_heading)
        if type(self.chunk_ordinal) is not int or self.chunk_ordinal < 0:
            raise CapturedChunkError("CAPTURED_CHUNK_ORDINAL_INVALID")
        if type(self.source_url) is not CanonicalURL:
            raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_URL_INVALID")
        if self.capture_status not in (CaptureStatus.SUCCESS, CaptureStatus.STALE):
            raise CapturedChunkError("CAPTURED_CHUNK_STATUS_NOT_ALLOWED")
        _validate_metadata_timestamps(self)
        if type(self.searchable) is not bool or not self.searchable:
            raise CapturedChunkError("CAPTURED_CHUNK_NOT_SEARCHABLE")
        if type(self.source_lineage) is not CapturedChunkSourceLineage:
            raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_LINEAGE_INVALID")

    @property
    def captured_content_id(self) -> CapturedContentId:
        return self.revision_ref.captured_content_id

    @property
    def content_hash(self) -> CaptureContentHash:
        return self.revision_ref.content_hash

    @property
    def parser_version(self) -> str:
        return self.revision_ref.parser_version

    @property
    def sync_batch_id(self) -> str:
        return self.source_lineage.sync_batch_id

    def __repr__(self) -> str:
        return (
            "CapturedChunkMetadata("
            f"chunk_id={str(self.chunk_id)!r}, "
            f"captured_content_id={str(self.captured_content_id)!r}, "
            f"authority_role={self.authority_role.value!r}, "
            f"capture_status={self.capture_status.value!r}, "
            f"chunk_ordinal={self.chunk_ordinal!r}, "
            "title=<redacted>, "
            "section_heading=<redacted>, "
            "source_url=<redacted>)"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class CapturedChunk:
    """One exact chunk body nested with its body-free metadata."""

    text: str
    metadata: CapturedChunkMetadata

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text.strip():
            raise CapturedChunkError("CAPTURED_CHUNK_TEXT_INVALID")
        if type(self.metadata) is not CapturedChunkMetadata:
            raise CapturedChunkError("CAPTURED_CHUNK_METADATA_INVALID")
        expected_id = _build_chunk_id(
            revision_ref=self.metadata.revision_ref,
            section_anchor=self.metadata.section_anchor,
            text=self.text,
        )
        if self.metadata.chunk_id != expected_id:
            raise CapturedChunkError("CAPTURED_CHUNK_IDENTITY_MISMATCH")

    def __repr__(self) -> str:
        return f"CapturedChunk(text=<redacted>, metadata={self.metadata!r})"

    __str__ = __repr__


def _build_canonical_chunk_constructors():
    authorization = object()

    def captured_chunk_metadata_init(
        self,
        *,
        chunk_id: CapturedChunkId,
        revision_ref: CaptureRevisionRef,
        asset_key: Optional[ContentAssetKey],
        metric_id: Optional[MetricId],
        evidence_relationship_id: Optional[EvidenceRelationshipId],
        brand_id: Optional[BrandId],
        source_record_id: Optional[SourceRecordId],
        authority_role: AuthorityRole,
        title: Optional[str],
        section_anchor: SectionAnchor,
        section_heading: Optional[str],
        chunk_ordinal: int,
        source_url: CanonicalURL,
        capture_status: CaptureStatus,
        captured_at: datetime,
        last_successful_capture_at: datetime,
        last_capture_attempt_at: datetime,
        searchable: bool,
        source_lineage: CapturedChunkSourceLineage,
        _wp13_gate=None,
    ) -> None:
        if _wp13_gate is not authorization:
            raise CapturedChunkError(
                "CAPTURED_CHUNK_METADATA_REQUIRES_BUILDER"
            )
        values = (
            ("chunk_id", chunk_id),
            ("revision_ref", revision_ref),
            ("asset_key", asset_key),
            ("metric_id", metric_id),
            ("evidence_relationship_id", evidence_relationship_id),
            ("brand_id", brand_id),
            ("source_record_id", source_record_id),
            ("authority_role", authority_role),
            ("title", title),
            ("section_anchor", section_anchor),
            ("section_heading", section_heading),
            ("chunk_ordinal", chunk_ordinal),
            ("source_url", source_url),
            ("capture_status", capture_status),
            ("captured_at", captured_at),
            ("last_successful_capture_at", last_successful_capture_at),
            ("last_capture_attempt_at", last_capture_attempt_at),
            ("searchable", searchable),
            ("source_lineage", source_lineage),
        )
        for name, value in values:
            object.__setattr__(self, name, value)
        self.__post_init__()

    def captured_chunk_init(
        self,
        *,
        text: str,
        metadata: CapturedChunkMetadata,
        _wp13_gate=None,
    ) -> None:
        if _wp13_gate is not authorization:
            raise CapturedChunkError("CAPTURED_CHUNK_REQUIRES_BUILDER")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "metadata", metadata)
        self.__post_init__()

    def create_captured_chunk_metadata(**values) -> CapturedChunkMetadata:
        return CapturedChunkMetadata(_wp13_gate=authorization, **values)

    def create_captured_chunk(
        *,
        text: str,
        metadata: CapturedChunkMetadata,
    ) -> CapturedChunk:
        return CapturedChunk(
            text=text,
            metadata=metadata,
            _wp13_gate=authorization,
        )

    captured_chunk_metadata_init.__name__ = "__init__"
    captured_chunk_metadata_init.__qualname__ = "CapturedChunkMetadata.__init__"
    captured_chunk_init.__name__ = "__init__"
    captured_chunk_init.__qualname__ = "CapturedChunk.__init__"
    return (
        captured_chunk_metadata_init,
        captured_chunk_init,
        create_captured_chunk_metadata,
        create_captured_chunk,
    )


(
    CapturedChunkMetadata.__init__,
    CapturedChunk.__init__,
    _create_captured_chunk_metadata,
    _create_captured_chunk,
) = _build_canonical_chunk_constructors()
del _build_canonical_chunk_constructors


def build_captured_chunk(
    *,
    captured_content: CapturedContent,
    revision_ref: CaptureRevisionRef,
    span: SyntheticChunkSpan,
    primary_source_record: Optional[SourceRecord] = None,
    stale_lkg_input: Optional[LkgEligibilityInput] = None,
    stale_lkg_result: Optional[LkgEligibilityResult] = None,
) -> CapturedChunk:
    """Build one chunk from an exact caller-injected canonical body span."""

    if type(captured_content) is not CapturedContent:
        raise CapturedChunkError("CAPTURED_CONTENT_REQUIRED")
    if type(revision_ref) is not CaptureRevisionRef:
        raise CapturedChunkError("CAPTURED_CHUNK_REVISION_REF_INVALID")
    if type(span) is not SyntheticChunkSpan:
        raise CapturedChunkError("SYNTHETIC_CHUNK_SPAN_REQUIRED")

    if captured_content.capture_status not in (
        CaptureStatus.SUCCESS,
        CaptureStatus.STALE,
    ):
        raise CapturedChunkError("CAPTURED_CHUNK_STATUS_NOT_ALLOWED")
    if captured_content.searchable is not True:
        raise CapturedChunkError("CAPTURED_CHUNK_NOT_SEARCHABLE")

    _validate_revision_binding(captured_content, revision_ref)
    _validate_stale_admission(
        captured_content,
        revision_ref,
        stale_lkg_input,
        stale_lkg_result,
    )
    _validate_span_binding(captured_content, span)
    brand_id, source_record_id = _derive_parent_context(
        captured_content,
        primary_source_record,
    )

    chunk_id = _build_chunk_id(
        revision_ref=revision_ref,
        section_anchor=span.section_anchor,
        text=span.text,
    )
    metadata = _create_captured_chunk_metadata(
        chunk_id=chunk_id,
        revision_ref=revision_ref,
        asset_key=captured_content.asset_key,
        metric_id=captured_content.metric_id,
        evidence_relationship_id=captured_content.evidence_relationship_id,
        brand_id=brand_id,
        source_record_id=source_record_id,
        authority_role=captured_content.authority_role,
        title=captured_content.title,
        section_anchor=span.section_anchor,
        section_heading=span.section_heading,
        chunk_ordinal=span.ordinal,
        source_url=captured_content.source_url,
        capture_status=captured_content.capture_status,
        captured_at=captured_content.captured_at,
        last_successful_capture_at=captured_content.last_successful_capture_at,
        last_capture_attempt_at=captured_content.last_capture_attempt_at,
        searchable=captured_content.searchable,
        source_lineage=_snapshot_source_lineage(
            captured_content.source_lineage
        ),
    )
    return _create_captured_chunk(text=span.text, metadata=metadata)


def _validate_revision_binding(
    captured_content: CapturedContent,
    revision_ref: CaptureRevisionRef,
) -> None:
    if (
        revision_ref.captured_content_id != captured_content.captured_content_id
        or str(revision_ref.content_hash) != captured_content.content_hash
        or revision_ref.parser_version != captured_content.parser_version
    ):
        raise CapturedChunkError("CAPTURED_CHUNK_REVISION_MISMATCH")


def _validate_stale_admission(
    captured_content: CapturedContent,
    revision_ref: CaptureRevisionRef,
    input_value: Optional[LkgEligibilityInput],
    result: Optional[LkgEligibilityResult],
) -> None:
    if captured_content.capture_status is CaptureStatus.SUCCESS:
        if input_value is not None or result is not None:
            raise CapturedChunkError("CAPTURED_CHUNK_STALE_PROOF_NOT_ALLOWED")
        return

    if type(input_value) is not LkgEligibilityInput:
        raise CapturedChunkError("CAPTURED_CHUNK_STALE_LKG_INPUT_REQUIRED")
    if type(result) is not LkgEligibilityResult:
        raise CapturedChunkError("CAPTURED_CHUNK_STALE_LKG_RESULT_REQUIRED")
    try:
        candidate = compose_stale_lkg(input_value, result)
    except ContentHashingError:
        raise CapturedChunkError("CAPTURED_CHUNK_STALE_PROOF_INVALID") from None
    if (
        candidate.capture_status is not CaptureStatus.STALE
        or candidate.revision_ref != revision_ref
        or candidate.revision_ref.captured_content_id
        != captured_content.captured_content_id
        or str(candidate.revision_ref.content_hash) != captured_content.content_hash
        or candidate.revision_ref.parser_version != captured_content.parser_version
        or candidate.captured_at != captured_content.captured_at
        or candidate.last_successful_capture_at
        != captured_content.last_successful_capture_at
        or candidate.last_capture_attempt_at
        != captured_content.last_capture_attempt_at
        or candidate.previous_content_hash != captured_content.previous_content_hash
        or candidate.searchable != captured_content.searchable
        or captured_content.canonical_url != input_value.current_canonical_url
    ):
        raise CapturedChunkError("CAPTURED_CHUNK_STALE_CANDIDATE_MISMATCH")


def _validate_span_binding(
    captured_content: CapturedContent,
    span: SyntheticChunkSpan,
) -> None:
    body = captured_content.clean_body
    if type(body) is not str or span.end > len(body):
        raise CapturedChunkError("CAPTURED_CHUNK_SPAN_OUT_OF_BOUNDS")
    if span.text != body[span.start : span.end]:
        raise CapturedChunkError("CAPTURED_CHUNK_SPAN_TEXT_MISMATCH")


def _derive_parent_context(
    captured_content: CapturedContent,
    primary_source_record: Optional[SourceRecord],
) -> Tuple[Optional[BrandId], Optional[SourceRecordId]]:
    if captured_content.authority_role is AuthorityRole.PRIMARY_CONTENT:
        if type(captured_content.asset_key) is not ContentAssetKey:
            raise CapturedChunkError("CAPTURED_CHUNK_PRIMARY_PARENT_INVALID")
        if type(primary_source_record) is not SourceRecord:
            raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_RECORD_REQUIRED")
        if (
            primary_source_record.source_record_id
            != captured_content.asset_key.source_record_id
        ):
            raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_RECORD_MISMATCH")
        if type(primary_source_record.brand_id) is not BrandId:
            raise CapturedChunkError("CAPTURED_CHUNK_PRIMARY_BRAND_REQUIRED")
        if (
            primary_source_record.source_lineage.sync_batch_id
            != captured_content.source_lineage.sync_batch_id
        ):
            raise CapturedChunkError(
                "CAPTURED_CHUNK_SOURCE_RECORD_BATCH_MISMATCH"
            )
        return primary_source_record.brand_id, primary_source_record.source_record_id

    if captured_content.authority_role is AuthorityRole.EVIDENCE:
        if primary_source_record is not None:
            raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_RECORD_NOT_ALLOWED")
        return None, None
    raise CapturedChunkError("CAPTURED_CHUNK_AUTHORITY_INVALID")


def _validate_metadata_authority(metadata: CapturedChunkMetadata) -> None:
    if metadata.authority_role is AuthorityRole.PRIMARY_CONTENT:
        if (
            type(metadata.asset_key) is not ContentAssetKey
            or metadata.metric_id is not None
            or metadata.evidence_relationship_id is not None
            or type(metadata.brand_id) is not BrandId
            or type(metadata.source_record_id) is not SourceRecordId
            or metadata.asset_key.source_record_id != metadata.source_record_id
        ):
            raise CapturedChunkError("CAPTURED_CHUNK_PRIMARY_ATTRIBUTION_INVALID")
        return
    if metadata.authority_role is AuthorityRole.EVIDENCE:
        if (
            metadata.asset_key is not None
            or type(metadata.metric_id) is not MetricId
            or type(metadata.evidence_relationship_id) is not EvidenceRelationshipId
            or metadata.brand_id is not None
            or metadata.source_record_id is not None
        ):
            raise CapturedChunkError("CAPTURED_CHUNK_EVIDENCE_ATTRIBUTION_INVALID")
        return
    raise CapturedChunkError("CAPTURED_CHUNK_AUTHORITY_INVALID")


def _validate_metadata_timestamps(metadata: CapturedChunkMetadata) -> None:
    values = (
        metadata.captured_at,
        metadata.last_successful_capture_at,
        metadata.last_capture_attempt_at,
    )
    if any(type(value) is not datetime or value.utcoffset() is None for value in values):
        raise CapturedChunkError("CAPTURED_CHUNK_TIMESTAMP_INVALID")
    if not (
        metadata.captured_at
        <= metadata.last_successful_capture_at
        <= metadata.last_capture_attempt_at
    ):
        raise CapturedChunkError("CAPTURED_CHUNK_TIMESTAMP_INVALID")


def _validate_section_heading(value: Optional[str]) -> None:
    if value is None:
        return
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or _contains_ascii_control(value)
        or len(value.splitlines()) != 1
    ):
        raise CapturedChunkError("SYNTHETIC_CHUNK_SECTION_HEADING_INVALID")


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _encode_utf8(value: str, error_code: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise CapturedChunkError(error_code) from None


def _frame(value: bytes) -> bytes:
    try:
        length = len(value).to_bytes(8, "big", signed=False)
    except OverflowError:
        raise CapturedChunkError("CAPTURED_CHUNK_FRAME_LENGTH_INVALID") from None
    return length + value


def _chunk_text_digest_payload(text: str) -> bytes:
    if type(text) is not str:
        raise CapturedChunkError("CAPTURED_CHUNK_TEXT_INVALID")
    text_bytes = _encode_utf8(text, "CAPTURED_CHUNK_TEXT_UTF8_INVALID")
    return _CHUNK_TEXT_DOMAIN + _frame(text_bytes)


def _compute_chunk_text_digest(text: str) -> bytes:
    return hashlib.sha256(_chunk_text_digest_payload(text)).digest()


def _build_chunk_id(
    *,
    revision_ref: CaptureRevisionRef,
    section_anchor: SectionAnchor,
    text: str,
) -> CapturedChunkId:
    if type(revision_ref) is not CaptureRevisionRef:
        raise CapturedChunkError("CAPTURED_CHUNK_REVISION_REF_INVALID")
    if type(section_anchor) is not SectionAnchor:
        raise CapturedChunkError("CAPTURED_CHUNK_SECTION_ANCHOR_INVALID")
    fields = (
        _encode_utf8(
            str(revision_ref.captured_content_id),
            "CAPTURED_CHUNK_ID_INPUT_UTF8_INVALID",
        ),
        _encode_utf8(
            str(revision_ref.content_hash),
            "CAPTURED_CHUNK_ID_INPUT_UTF8_INVALID",
        ),
        _encode_utf8(
            revision_ref.parser_version,
            "CAPTURED_CHUNK_ID_INPUT_UTF8_INVALID",
        ),
        _encode_utf8(
            str(section_anchor),
            "CAPTURED_CHUNK_ID_INPUT_UTF8_INVALID",
        ),
        _compute_chunk_text_digest(text),
    )
    payload = _CHUNK_ID_DOMAIN + b"".join(_frame(field) for field in fields)
    return CapturedChunkId(f"chk:v1:sha256:{hashlib.sha256(payload).hexdigest()}")


def _snapshot_source_lineage(
    lineage: CanonicalSourceLineage,
) -> CapturedChunkSourceLineage:
    if type(lineage) is not CanonicalSourceLineage:
        raise CapturedChunkError("CAPTURED_CHUNK_SOURCE_LINEAGE_INVALID")
    return CapturedChunkSourceLineage(
        spreadsheet_id_hash=lineage.spreadsheet_id_hash,
        sheet_id=lineage.sheet_id,
        sheet_title=lineage.sheet_title,
        source_row=lineage.source_row,
        source_columns=_validate_and_snapshot_lineage_mapping(
            lineage.source_columns,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_COLUMNS_INVALID",
        ),
        source_ranges=_validate_and_snapshot_lineage_mapping(
            lineage.source_ranges,
            "CAPTURED_CHUNK_LINEAGE_SOURCE_RANGES_INVALID",
        ),
        source_fingerprint=lineage.source_fingerprint,
        sync_batch_id=lineage.sync_batch_id,
    )


def _validate_lineage_text(value: object, error_code: str) -> None:
    if type(value) is not str or not value.strip():
        raise CapturedChunkError(error_code)


def _validate_and_snapshot_lineage_mapping(
    mapping: dict,
    error_code: str,
) -> Tuple[Tuple[str, str], ...]:
    entries = tuple(mapping.items())
    for key, value in entries:
        _validate_lineage_entry(key, value, error_code)
    return tuple(sorted(entries))


def _validate_lineage_entry(key: object, value: object, error_code: str) -> None:
    if (
        type(key) is not str
        or type(value) is not str
        or not key.strip()
        or not value.strip()
    ):
        raise CapturedChunkError(error_code)


def _validate_lineage_pairs(value: object, error_code: str) -> None:
    if type(value) is not tuple:
        raise CapturedChunkError(error_code)
    for entry in value:
        if (
            type(entry) is not tuple
            or len(entry) != 2
        ):
            raise CapturedChunkError(error_code)
        _validate_lineage_entry(entry[0], entry[1], error_code)
    if value != tuple(sorted(value)):
        raise CapturedChunkError(error_code)


__all__ = [
    "CapturedChunk",
    "CapturedChunkError",
    "CapturedChunkId",
    "CapturedChunkMetadata",
    "CapturedChunkSourceLineage",
    "SectionAnchor",
    "SyntheticChunkSpan",
    "build_captured_chunk",
]
