"""Offline Sprint 1 WP3 canonical normalization and governance staging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
import math
import re
import unicodedata
import weakref
from typing import Dict, Iterable, Optional, Tuple

from .brand_review_candidates import (
    BrandReviewCandidate,
    SafeSourceRef,
    _UntrustedBrandCandidateProjection,
    _UntrustedBrandEvidence,
    _candidate_ref_digest,
    _project_brand_review_candidates,
    _safe_hostname,
    _safe_source_ref_digest,
    _website_ref,
)
from .canonical_models import (
    CanonicalSourceLineage,
    ExposureChannel,
    LifecycleStatus,
    PublishEligibility,
    ReviewStatus,
)
from .cell_normalization import (
    CellNormalizationError,
    FieldContract,
    FieldValueKind,
    ResolvedCellValue,
    normalize_source_cell,
)
from .google_normalization import (
    ExcludedSourceRef,
    MetricMinimizationError,
    MetricSourceCells,
    minimize_public_metric_source,
)
from .google_sheets_dry_run_contracts import (
    CoverageProvenBatchContext,
    RunMode,
    SourceHealthDisposition,
    _context_result,
)
from .google_sheets_source_health import _validated_context_envelope
from .link_resolution import LinkExtractionError, _parse_hyperlink_first_argument
from .sheets_contracts import CellData, SheetSnapshot, SpreadsheetSnapshot
from .url_safety import URLValidationError, validate_and_canonicalize_evidence_url


ID_DIAGNOSTIC_SCHEMA_VERSION = "s1-wp3-id-diagnostic-v1"
_LITERAL_HTTP_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_TAG_SEPARATOR = re.compile(r"[,，、]")
_YEAR = re.compile(r"[0-9]{4}")
_DATE_TEXT = re.compile(r"([0-9]{4})([-/.])([0-9]{2})\2([0-9]{2})")
_GOOGLE_DATE_EPOCH = date(1899, 12, 30)


class CanonicalNormalizationError(ValueError):
    """Stable payload-free WP3 failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


@dataclass(frozen=True, eq=False)
class _ContextBinding:
    context: object
    result: object
    envelope: object
    target_identity_hash: str
    source_fingerprint: str
    sync_batch_id: str

    @property
    def context_identity(self) -> int:
        return id(self.context)

    @property
    def result_identity(self) -> int:
        return id(self.result)

    @property
    def envelope_identity(self) -> int:
        return id(self.envelope)


@dataclass(frozen=True)
class _TrustedObjectRecord:
    kind: str
    binding: _ContextBinding
    source_class: Optional[str] = None
    sheet_id: Optional[int] = None
    source_row: Optional[int] = None
    source_ref_identities: Tuple[int, ...] = ()


class IdentityState(str, Enum):
    SCHEMA_DEFERRED = "SCHEMA_DEFERRED"


class CandidateReviewState(str, Enum):
    NEEDS_REVIEW = "NEEDS_REVIEW"


class IdNamespace(str, Enum):
    MREC = "MREC"
    BRD = "BRD"
    MET = "MET"
    NONE = "NONE"


@dataclass(frozen=True)
class WP3FieldSpec:
    name: str
    column: str
    value_kind: str
    merge_inheritance_allowed: bool = False


@dataclass(frozen=True)
class WP3SheetSpec:
    source_class: str
    sheet_id: int
    title: str
    first_data_row: int
    last_data_row: int
    first_column: str
    last_column: str
    fields: Tuple[WP3FieldSpec, ...]


WP3_FIELD_REGISTRY = (
    WP3SheetSpec(
        "merchant_case", 0, "商家/夥伴案例資料庫", 7, 1018, "A", "L",
        (
            WP3FieldSpec("interview_year", "A", "YEAR_ONLY"),
            WP3FieldSpec("source_status", "B", "TEXT"),
            WP3FieldSpec("merchant_name", "C", "TEXT_LINK"),
            WP3FieldSpec("normalized_handle", "D", "HANDLE"),
            WP3FieldSpec("sales_category_lv1", "E", "TEXT"),
            WP3FieldSpec("sales_category_lv2", "F", "TEXT"),
            WP3FieldSpec("content_tags", "G", "TAGS"),
            WP3FieldSpec("article", "H", "TEXT_LINK"),
            WP3FieldSpec("video", "I", "TEXT_LINK"),
            WP3FieldSpec("podcast", "J", "TEXT_LINK"),
            WP3FieldSpec("news", "K", "TEXT_LINK"),
            WP3FieldSpec("notes", "L", "TEXT"),
        ),
    ),
    WP3SheetSpec(
        "restricted_customer", 1456785208, "「不可公開」客戶名單", 5, 994, "A", "H",
        (
            WP3FieldSpec("updated_year", "A", "YEAR_ONLY"),
            WP3FieldSpec("customer_brand", "B", "TEXT"),
            WP3FieldSpec("website", "C", "TEXT_LINK"),
            WP3FieldSpec("sales_category_lv1", "D", "TEXT"),
            WP3FieldSpec("nda_signed", "E", "BOOLEAN"),
            WP3FieldSpec("nda_uploaded", "F", "BOOLEAN"),
            WP3FieldSpec("restricted_reason", "G", "TEXT"),
            WP3FieldSpec("submitted_by", "H", "TEXT"),
        ),
    ),
    WP3SheetSpec(
        "public_metric", 918878896, "「可公開」對外數據", 7, 999, "A", "M",
        (
            WP3FieldSpec("metric_type", "A", "TEXT", merge_inheritance_allowed=True),
            WP3FieldSpec("indicator", "B", "TEXT", merge_inheritance_allowed=True),
            WP3FieldSpec("statement", "C", "TEXT"),
            WP3FieldSpec("note", "D", "TEXT"),
            WP3FieldSpec("updated_date", "E", "DATE_ONLY"),
            WP3FieldSpec("evidence_url", "F", "TEXT_LINK", merge_inheritance_allowed=True),
            WP3FieldSpec("press_release", "G", "BOOLEAN"),
            WP3FieldSpec("owned_media", "H", "BOOLEAN"),
            WP3FieldSpec("saleskits", "I", "BOOLEAN"),
            WP3FieldSpec("verbal_briefing", "J", "BOOLEAN"),
            WP3FieldSpec("speaking_deck", "K", "BOOLEAN"),
            WP3FieldSpec("website_recruiting", "L", "BOOLEAN"),
            WP3FieldSpec("ads", "M", "BOOLEAN"),
        ),
    ),
    WP3SheetSpec(
        "pending_metric", 956677822, "待確認數據", 3, 999, "A", "D",
        (
            WP3FieldSpec("metric_type", "A", "TEXT"),
            WP3FieldSpec("indicator", "B", "TEXT"),
            WP3FieldSpec("statement", "C", "TEXT"),
            WP3FieldSpec("note", "D", "TEXT"),
        ),
    ),
    WP3SheetSpec(
        "handle_mapping", 737692182, "handle 比對", 2, 998, "A", "D",
        (
            WP3FieldSpec("normalized_handle", "A", "HANDLE"),
            WP3FieldSpec("name_with_link", "B", "TEXT_LINK"),
            WP3FieldSpec("category_lv1", "C", "TEXT"),
            WP3FieldSpec("category_lv2", "D", "TEXT"),
        ),
    ),
)

_SPECS = {item.source_class: item for item in WP3_FIELD_REGISTRY}
_CHANNEL_FIELDS = (
    ("press_release", ExposureChannel.PRESS_RELEASE),
    ("owned_media", ExposureChannel.OWNED_MEDIA),
    ("saleskits", ExposureChannel.SALESKITS),
    ("verbal_briefing", ExposureChannel.VERBAL_BRIEFING),
    ("speaking_deck", ExposureChannel.SPEAKING_DECK),
    ("website_recruiting", ExposureChannel.WEBSITE_RECRUITING),
    ("ads", ExposureChannel.ADS),
)


class _SensitiveStaging:
    """Application correctness/data boundary, not same-process isolation."""

    # Guard scope: docs/governance/PYTHON_GUARD_THREAT_MODEL.md (T1/T2, not T3).
    __slots__ = ("__weakref__",)

    def __new__(cls, *args: object, **kwargs: object) -> "_SensitiveStaging":
        raise TypeError(f"{cls.__name__.upper()}_CONSTRUCTION_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WP3_SENSITIVE_STAGING_IMMUTABLE")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(content=<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("WP3_SENSITIVE_STAGING_PICKLE_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("WP3_SENSITIVE_STAGING_PICKLE_FORBIDDEN")

    def __getstate__(self) -> object:
        raise TypeError("WP3_SENSITIVE_STAGING_SERIALIZATION_FORBIDDEN")


class AssetCellStaging(_SensitiveStaging):
    __slots__ = ("_field_name", "_text", "_canonical_urls", "_unsafe_url", "_lineage")

    field_name = property(lambda self: self._field_name)
    text = property(lambda self: self._text)
    canonical_urls = property(lambda self: self._canonical_urls)
    unsafe_url = property(lambda self: self._unsafe_url)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))


class MerchantCaseStaging(_SensitiveStaging):
    __slots__ = (
        "_source_ref", "_lineage", "_interview_year", "_source_status", "_name",
        "_normalized_handle", "_sales_category_lv1", "_sales_category_lv2", "_tags",
        "_asset_cells", "_notes",
    )

    source_ref = property(lambda self: self._source_ref)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))
    interview_year = property(lambda self: self._interview_year)
    source_status = property(lambda self: self._source_status)
    name = property(lambda self: self._name)
    normalized_handle = property(lambda self: self._normalized_handle)
    sales_category_lv1 = property(lambda self: self._sales_category_lv1)
    sales_category_lv2 = property(lambda self: self._sales_category_lv2)
    tags = property(lambda self: self._tags)
    asset_cells = property(lambda self: self._asset_cells)
    notes = property(lambda self: self._notes)
    identity_state = property(lambda self: IdentityState.SCHEMA_DEFERRED)
    brand_state = property(lambda self: CandidateReviewState.NEEDS_REVIEW)
    publish_eligibility = property(lambda self: PublishEligibility.NEEDS_REVIEW)


class RestrictedDenylistStaging(_SensitiveStaging):
    __slots__ = (
        "_source_ref", "_lineage", "_updated_year", "_customer_brand", "_website_text",
        "_canonical_urls", "_sales_category_lv1", "_nda_signed", "_nda_uploaded",
        "_restricted_reason", "_submitted_by",
    )

    source_ref = property(lambda self: self._source_ref)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))
    updated_year = property(lambda self: self._updated_year)
    customer_brand = property(lambda self: self._customer_brand)
    website_text = property(lambda self: self._website_text)
    canonical_urls = property(lambda self: self._canonical_urls)
    sales_category_lv1 = property(lambda self: self._sales_category_lv1)
    nda_signed = property(lambda self: self._nda_signed)
    nda_uploaded = property(lambda self: self._nda_uploaded)
    restricted_reason = property(lambda self: self._restricted_reason)
    submitted_by = property(lambda self: self._submitted_by)


class PublicMetricStaging(_SensitiveStaging):
    __slots__ = (
        "_source_ref", "_lineage", "_metric_type", "_indicator", "_statement", "_note",
        "_maintenance_updated_at", "_evidence_urls", "_allowed_exposure_channels",
    )

    source_ref = property(lambda self: self._source_ref)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))
    metric_type = property(lambda self: self._metric_type)
    indicator = property(lambda self: self._indicator)
    statement = property(lambda self: self._statement)
    note = property(lambda self: self._note)
    maintenance_updated_at = property(lambda self: self._maintenance_updated_at)
    evidence_urls = property(lambda self: self._evidence_urls)
    allowed_exposure_channels = property(lambda self: self._allowed_exposure_channels)
    identity_state = property(lambda self: IdentityState.SCHEMA_DEFERRED)
    review_status = property(lambda self: ReviewStatus.NEEDS_REVIEW)
    publish_eligibility = property(lambda self: PublishEligibility.NEEDS_REVIEW)
    can_quote_externally = property(lambda self: False)


class PendingMetricStaging(_SensitiveStaging):
    __slots__ = ("_source_ref", "_lineage", "_metric_type", "_indicator", "_statement", "_note")

    source_ref = property(lambda self: self._source_ref)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))
    metric_type = property(lambda self: self._metric_type)
    indicator = property(lambda self: self._indicator)
    statement = property(lambda self: self._statement)
    note = property(lambda self: self._note)
    lifecycle_status = property(lambda self: "DRAFT")
    exposure = property(lambda self: "INTERNAL_REVIEW_ONLY")
    authority = property(lambda self: "NON_OFFICIAL")
    can_quote_externally = property(lambda self: False)
    can_publish = property(lambda self: False)
    metric_kind = property(lambda self: "NOT_PUBLIC_METRIC")


class HandleMappingStaging(_SensitiveStaging):
    __slots__ = (
        "_source_ref", "_lineage", "_normalized_handle", "_name", "_canonical_urls",
        "_category_lv1", "_category_lv2",
    )

    source_ref = property(lambda self: self._source_ref)
    source_lineage = property(lambda self: _clone_lineage(self._lineage))
    normalized_handle = property(lambda self: self._normalized_handle)
    name = property(lambda self: self._name)
    canonical_urls = property(lambda self: self._canonical_urls)
    category_lv1 = property(lambda self: self._category_lv1)
    category_lv2 = property(lambda self: self._category_lv2)
    evidence_authority = property(lambda self: "EVIDENCE_ONLY")


class _SafeFact:
    __slots__ = ("__weakref__",)

    def __new__(cls, *args: object, **kwargs: object) -> "_SafeFact":
        raise TypeError(f"{cls.__name__.upper()}_CONSTRUCTION_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WP3_SAFE_FACT_IMMUTABLE")

    def __reduce__(self) -> object:
        raise TypeError("WP3_SAFE_FACT_SERIALIZATION_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("WP3_SAFE_FACT_SERIALIZATION_FORBIDDEN")


class RestrictedGovernanceFact(_SafeFact):
    """Declarative governance facts; consumers must explicitly enforce them."""

    __slots__ = ("_source_ref", "_reason_codes", "_identity_term_count")

    source_ref = property(lambda self: self._source_ref)
    sheet_id = property(lambda self: self._source_ref.sheet_id)
    source_row = property(lambda self: self._source_ref.source_row)
    reason_codes = property(lambda self: self._reason_codes)
    identity_term_count = property(lambda self: self._identity_term_count)
    can_quote_externally = property(lambda self: False)
    can_enter_general_staging = property(lambda self: False)
    can_enter_brand_review = property(lambda self: False)
    can_enter_retrieval = property(lambda self: False)
    can_publish = property(lambda self: False)


class PendingMetricGovernanceFact(_SafeFact):
    __slots__ = ("_source_ref",)

    source_ref = property(lambda self: self._source_ref)
    sheet_id = property(lambda self: self._source_ref.sheet_id)
    source_row = property(lambda self: self._source_ref.source_row)
    lifecycle_status = property(lambda self: "DRAFT")
    exposure = property(lambda self: "INTERNAL_REVIEW_ONLY")
    authority = property(lambda self: "NON_OFFICIAL")
    can_quote_externally = property(lambda self: False)
    can_publish = property(lambda self: False)
    metric_kind = property(lambda self: "NOT_PUBLIC_METRIC")


class SourceReviewFact(_SafeFact):
    __slots__ = ("_source_ref", "_reason_codes")

    source_ref = property(lambda self: self._source_ref)
    reason_codes = property(lambda self: self._reason_codes)


class DuplicateMerchantReviewFact(_SafeFact):
    __slots__ = ("_source_refs", "_reason_code")

    source_refs = property(lambda self: self._source_refs)
    reason_code = property(lambda self: self._reason_code)


class IdDiagnostic(_SafeFact):
    __slots__ = ("_namespace", "_source_class", "_field_name_or_surface", "_reason_code")

    schema_version = property(lambda self: ID_DIAGNOSTIC_SCHEMA_VERSION)
    authority = property(lambda self: "NON_AUTHORITATIVE")
    scope = property(lambda self: "BATCH")
    diagnostic_kind = property(lambda self: "SCHEMA_DEFERRED")
    namespace = property(lambda self: self._namespace)
    source_class = property(lambda self: self._source_class)
    field_name_or_surface = property(lambda self: self._field_name_or_surface)
    reason_code = property(lambda self: self._reason_code)


class CanonicalNormalizationBatch(_SensitiveStaging):
    __slots__ = (
        "_merchant_cases", "_restricted_denylist", "_restricted_facts", "_public_metrics",
        "_excluded_public_metrics", "_pending_metrics", "_pending_facts", "_handle_mappings",
        "_brand_review_candidates", "_duplicate_review_facts", "_review_facts", "_id_diagnostics",
    )

    merchant_cases = property(lambda self: self._merchant_cases)
    restricted_denylist = property(lambda self: self._restricted_denylist)
    restricted_facts = property(lambda self: self._restricted_facts)
    public_metrics = property(lambda self: self._public_metrics)
    excluded_public_metrics = property(lambda self: self._excluded_public_metrics)
    pending_metrics = property(lambda self: self._pending_metrics)
    pending_facts = property(lambda self: self._pending_facts)
    handle_mappings = property(lambda self: self._handle_mappings)
    brand_review_candidates = property(lambda self: self._brand_review_candidates)
    duplicate_review_facts = property(lambda self: self._duplicate_review_facts)
    review_facts = property(lambda self: self._review_facts)
    id_diagnostics = property(lambda self: self._id_diagnostics)

    def __repr__(self) -> str:
        return (
            "CanonicalNormalizationBatch("
            f"merchant_count={len(self.merchant_cases)}, "
            f"restricted_count={len(self.restricted_facts)}, "
            f"public_metric_count={len(self.public_metrics)}, "
            f"pending_count={len(self.pending_facts)}, "
            f"brand_candidate_count={len(self.brand_review_candidates)}, "
            "content=<redacted>)"
        )


def _merchant_rows(snapshot, envelope, builder):
    spec = _SPECS["merchant_case"]
    staging = []
    evidence = []
    reviews = []
    duplicate_keys: Dict[tuple, list] = {}
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder)
        source_ref = builder.source_ref(spec, row_index)
        name = _text(resolved.get("merchant_name"))
        handle = _handle(resolved.get("normalized_handle"))
        year = _year_value(resolved.get("interview_year"), reviews, source_ref, builder)
        name_urls, name_unsafe = _links(resolved.get("merchant_name"))
        if name is None:
            reviews.append(_review(source_ref, ("MERCHANT_NAME_MISSING",), builder))
        assets = []
        duplicate_asset_values = []
        for field_name in ("article", "video", "podcast", "news"):
            cell = resolved.get(field_name)
            urls, unsafe = _links(cell)
            text = _text(cell)
            lineage = _lineage(spec, row_index, {field_name: cell}, envelope)
            assets.append(
                builder.new(
                    AssetCellStaging, field_name=field_name, text=text,
                    canonical_urls=urls, unsafe_url=unsafe, lineage=lineage,
                )
            )
            duplicate_asset_values.append((text, urls))
        lineage = _lineage(spec, row_index, resolved, envelope)
        item = builder.new(
            MerchantCaseStaging,
            source_ref=source_ref,
            lineage=lineage,
            interview_year=year,
            source_status=_text(resolved.get("source_status")),
            name=name,
            normalized_handle=handle,
            sales_category_lv1=_text(resolved.get("sales_category_lv1")),
            sales_category_lv2=_text(resolved.get("sales_category_lv2")),
            tags=_tags(_text(resolved.get("content_tags"))),
            asset_cells=tuple(assets),
            notes=_text(resolved.get("notes")),
        )
        staging.append(item)
        evidence.append(
            _UntrustedBrandEvidence(
                source_ref=source_ref,
                normalized_name=_name_key(name),
                normalized_handle=handle,
                canonical_urls=name_urls,
                unsafe_website_evidence=name_unsafe,
                handle_mapping=False,
                multiple_urls_in_one_cell=len(name_urls) > 1,
            )
        )
        duplicate_key = (name, handle, year, *duplicate_asset_values)
        identity_or_asset_present = (
            name is not None
            or handle is not None
            or any(text is not None or bool(urls) for text, urls in duplicate_asset_values)
        )
        if identity_or_asset_present:
            duplicate_keys.setdefault(duplicate_key, []).append(source_ref)
    duplicate_facts = []
    for refs in duplicate_keys.values():
        if len(refs) > 1:
            duplicate_facts.append(
                builder.new(
                    DuplicateMerchantReviewFact,
                    source_refs=tuple(sorted(refs, key=lambda item: item.source_ref)),
                    reason_code="DUPLICATE_MERCHANT_INTERVIEW_REVIEW",
                )
            )
    return tuple(staging), tuple(evidence), tuple(duplicate_facts), tuple(reviews)


def _restricted_rows(snapshot, envelope, builder):
    spec = _SPECS["restricted_customer"]
    staging = []
    facts = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder)
        source_ref = builder.source_ref(spec, row_index)
        brand = _text(resolved.get("customer_brand"))
        website = _text(resolved.get("website"))
        urls, _unsafe = _links(resolved.get("website"))
        reason_codes = ["RESTRICTED_SOURCE"]
        if brand is None:
            reason_codes.append("RESTRICTED_IDENTITY_MISSING")
        lineage = _lineage(spec, row_index, resolved, envelope)
        staging.append(
            builder.new(
                RestrictedDenylistStaging,
                source_ref=source_ref,
                lineage=lineage,
                updated_year=_year_value(resolved.get("updated_year"), reviews, source_ref, builder),
                customer_brand=brand,
                website_text=website,
                canonical_urls=urls,
                sales_category_lv1=_text(resolved.get("sales_category_lv1")),
                nda_signed=_optional_bool(resolved.get("nda_signed")),
                nda_uploaded=_optional_bool(resolved.get("nda_uploaded")),
                restricted_reason=_text(resolved.get("restricted_reason")),
                submitted_by=_text(resolved.get("submitted_by")),
            )
        )
        facts.append(
            builder.new(
                RestrictedGovernanceFact,
                source_ref=source_ref,
                reason_codes=tuple(sorted(reason_codes)),
                identity_term_count=int(brand is not None)
                + int(website is not None or bool(urls)),
            )
        )
    return tuple(staging), tuple(facts), tuple(reviews)


def _public_metric_rows(snapshot, envelope, builder):
    spec = _SPECS["public_metric"]
    staging = []
    exclusions = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        review_count_before_resolution = len(reviews)
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder)
        source_normalization_failed = len(reviews) != review_count_before_resolution
        source_ref = builder.source_ref(spec, row_index)
        statement = _text(resolved.get("statement"))
        if statement is None:
            reviews.append(_review(source_ref, ("PUBLIC_METRIC_STATEMENT_MISSING",), builder))
            continue
        metric_type = _text(resolved.get("metric_type"))
        indicator = _text(resolved.get("indicator"))
        note = _text(resolved.get("note"))
        evidence_urls, evidence_unsafe = _links(resolved.get("evidence_url"))
        precheck_codes = []
        if source_normalization_failed:
            precheck_codes.append("PUBLIC_METRIC_SOURCE_NORMALIZATION_FAILED")
        if metric_type is None or indicator is None:
            precheck_codes.append("PUBLIC_METRIC_REQUIRED_CLASSIFICATION_MISSING")
        maintenance_date = None
        try:
            maintenance_date = _date_value(resolved.get("updated_date"))
        except CanonicalNormalizationError:
            precheck_codes.append("PUBLIC_METRIC_DATE_NEEDS_REVIEW")
        if evidence_unsafe:
            precheck_codes.append("PUBLIC_METRIC_EVIDENCE_URL_UNSAFE")
        if len(evidence_urls) > 1:
            precheck_codes.append("PUBLIC_METRIC_MULTIPLE_EVIDENCE_URLS")

        channel_cells = tuple(resolved.get(field_name) for field_name, _ in _CHANNEL_FIELDS)
        channels = []
        channel_valid = True
        for cell, (_field_name, channel) in zip(channel_cells, _CHANNEL_FIELDS):
            if not _governed_bool(cell):
                channel_valid = False
            elif cell.normalized_value:
                channels.append(channel)
        if not channel_valid:
            precheck_codes.append("PUBLIC_METRIC_CHANNEL_GOVERNANCE_UNCERTAIN")

        lineage = _lineage(spec, row_index, resolved, envelope)
        source = MetricSourceCells(
            metric_id=None,
            metric_type=metric_type,
            indicator=indicator,
            approved_statement=statement,
            note=note,
            maintenance_updated_at=maintenance_date,
            evidence_urls=evidence_urls,
            channel_cells=channel_cells,
            lifecycle_status=LifecycleStatus.CANDIDATE,
            review_status=ReviewStatus.NEEDS_REVIEW,
            publish_eligibility=PublishEligibility.NEEDS_REVIEW,
            can_quote_externally=False,
            source_lineage=lineage,
        )
        try:
            minimized = minimize_public_metric_source(source)
        except MetricMinimizationError as exc:
            if exc.code == "METRIC_ID_REQUIRED_FOR_PERSISTENCE" and not precheck_codes:
                staging.append(
                    builder.new(
                        PublicMetricStaging,
                        source_ref=source_ref,
                        lineage=lineage,
                        metric_type=metric_type,
                        indicator=indicator,
                        statement=statement,
                        note=note,
                        maintenance_updated_at=maintenance_date,
                        evidence_urls=evidence_urls,
                        allowed_exposure_channels=tuple(channels),
                    )
                )
            else:
                codes = tuple(sorted(set(precheck_codes or (exc.code,))))
                reviews.append(_review(source_ref, codes, builder))
        else:
            if type(minimized) is not ExcludedSourceRef:
                _fail("WP3_FINAL_METRIC_OBJECT_FORBIDDEN")
            exclusions.append(minimized)
    return tuple(staging), tuple(exclusions), tuple(reviews)


def _pending_rows(snapshot, envelope, builder):
    spec = _SPECS["pending_metric"]
    staging = []
    facts = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder)
        source_ref = builder.source_ref(spec, row_index)
        statement = _text(resolved.get("statement"))
        if statement is None:
            reviews.append(_review(source_ref, ("PENDING_METRIC_STATEMENT_MISSING",), builder))
        staging.append(
            builder.new(
                PendingMetricStaging,
                source_ref=source_ref,
                lineage=_lineage(spec, row_index, resolved, envelope),
                metric_type=_text(resolved.get("metric_type")),
                indicator=_text(resolved.get("indicator")),
                statement=statement,
                note=_text(resolved.get("note")),
            )
        )
        facts.append(builder.new(PendingMetricGovernanceFact, source_ref=source_ref))
    return tuple(staging), tuple(facts), tuple(reviews)


def _handle_mapping_rows(snapshot, envelope, builder):
    spec = _SPECS["handle_mapping"]
    staging = []
    evidence = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder)
        source_ref = builder.source_ref(spec, row_index)
        handle = _handle(resolved.get("normalized_handle"))
        name = _text(resolved.get("name_with_link"))
        urls, unsafe = _links(resolved.get("name_with_link"))
        if handle is None and not urls:
            reviews.append(_review(source_ref, ("HANDLE_MAPPING_IDENTITY_EVIDENCE_MISSING",), builder))
        staging.append(
            builder.new(
                HandleMappingStaging,
                source_ref=source_ref,
                lineage=_lineage(spec, row_index, resolved, envelope),
                normalized_handle=handle,
                name=name,
                canonical_urls=urls,
                category_lv1=_text(resolved.get("category_lv1")),
                category_lv2=_text(resolved.get("category_lv2")),
            )
        )
        evidence.append(
            _UntrustedBrandEvidence(
                source_ref=source_ref,
                normalized_name=_name_key(name),
                normalized_handle=handle,
                canonical_urls=urls,
                unsafe_website_evidence=unsafe,
                handle_mapping=True,
                multiple_urls_in_one_cell=len(urls) > 1,
            )
        )
    return tuple(staging), tuple(evidence), tuple(reviews)


def _resolve_fields(snapshot, envelope, spec, row_index, reviews, builder):
    resolved = {}
    source_ref = builder.source_ref(spec, row_index)
    for field in spec.fields:
        value_kind = FieldValueKind.BOOLEAN if field.value_kind == "BOOLEAN" else FieldValueKind.TEXT
        try:
            resolved[field.name] = normalize_source_cell(
                snapshot,
                sheet_id=spec.sheet_id,
                source_row_index=row_index,
                field_contract=FieldContract(
                    field.name,
                    value_kind,
                    _column_index(field.column),
                    field.merge_inheritance_allowed,
                ),
                source_fingerprint=envelope.source_fingerprint,
                sync_batch_id=envelope.correlation_id,
            )
        except CellNormalizationError as exc:
            reviews.append(_review(source_ref, (exc.code,), builder))
            resolved[field.name] = None
    return resolved


def _occupied_rows(snapshot: SpreadsheetSnapshot, spec: WP3SheetSpec) -> Tuple[int, ...]:
    sheet = _sheet(snapshot, spec.sheet_id)
    first = spec.first_data_row - 1
    last = spec.last_data_row - 1
    first_column = _column_index(spec.first_column)
    last_column = _column_index(spec.last_column)
    rows = {
        cell.row_index
        for cell in sheet.cells
        if first <= cell.row_index <= last
        and first_column <= cell.column_index <= last_column
        and _cell_has_source_data(cell)
    }
    return tuple(sorted(rows))


def _cell_has_source_data(cell: CellData) -> bool:
    return any(
        (
            cell.formatted_value not in (None, ""),
            cell.effective_value is not None,
            cell.user_entered_value is not None,
            cell.hyperlink is not None,
            bool(cell.text_format_runs),
        )
    )


def _text(cell: Optional[ResolvedCellValue]) -> Optional[str]:
    if cell is None or cell.normalized_value is None:
        return None
    if type(cell.normalized_value) is not str:
        _fail("WP3_TEXT_VALUE_TYPE_INVALID")
    value = unicodedata.normalize("NFKC", cell.normalized_value).strip()
    return value or None


def _handle(cell: Optional[ResolvedCellValue]) -> Optional[str]:
    if cell is None or cell.normalized_value is None:
        return None
    if type(cell.normalized_value) is not str:
        _fail("WP3_TEXT_VALUE_TYPE_INVALID")
    normalized = unicodedata.normalize("NFKC", cell.normalized_value)
    if any(
        character in "\t\n\r"
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in normalized
    ):
        return None
    normalized = normalized.strip().casefold()
    return normalized if _safe_handle_value(normalized) else None


def _safe_handle_value(normalized: object) -> bool:
    if type(normalized) is not str or not normalized or len(normalized) > 128:
        return False
    if normalized != unicodedata.normalize("NFKC", normalized).strip().casefold():
        return False
    body = normalized[1:] if normalized.startswith("@") else normalized
    if not body or "@" in body:
        return False
    has_identity_character = False
    for character in body:
        category = unicodedata.category(character)
        if category.startswith(("L", "N")):
            has_identity_character = True
        elif character not in "._-":
            return False
    return has_identity_character


def _name_key(value: Optional[str]) -> Optional[str]:
    return value.casefold() if value is not None else None


def _tags(value: Optional[str]) -> Tuple[str, ...]:
    if value is None:
        return ()
    output = []
    seen = set()
    for part in _TAG_SEPARATOR.split(value):
        normalized = unicodedata.normalize("NFKC", part).strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return tuple(output)


def _year_value(cell, reviews, source_ref, builder) -> Optional[int]:
    text = _text(cell)
    if text is None:
        return None
    if _YEAR.fullmatch(text) and 1000 <= int(text) <= 9999:
        return int(text)
    reviews.append(_review(source_ref, ("YEAR_ONLY_NEEDS_REVIEW",), builder))
    return None


def _date_value(cell: Optional[ResolvedCellValue]) -> Optional[date]:
    if cell is None or cell.normalized_value is None:
        return None
    value_cell = cell.value_cell
    effective = value_cell.effective_value if value_cell is not None else None
    number = effective.number_value if effective is not None else None
    text = effective.string_value if effective is not None else None
    if number is not None:
        if (
            type(number) is bool
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number < 0
            or int(number) != number
        ):
            _fail("DATE_ONLY_NEEDS_REVIEW")
        try:
            return _GOOGLE_DATE_EPOCH + timedelta(days=int(number))
        except (OverflowError, ValueError):
            _fail("DATE_ONLY_NEEDS_REVIEW")
    if cell.source_was_formula:
        _fail("DATE_ONLY_NEEDS_REVIEW")
    text_value = text if type(text) is str else cell.display_value
    if type(text_value) is not str:
        _fail("DATE_ONLY_NEEDS_REVIEW")
    normalized = unicodedata.normalize("NFKC", text_value).strip()
    matched = _DATE_TEXT.fullmatch(normalized)
    if matched is None:
        _fail("DATE_ONLY_NEEDS_REVIEW")
    try:
        return date(int(matched.group(1)), int(matched.group(3)), int(matched.group(4)))
    except ValueError:
        _fail("DATE_ONLY_NEEDS_REVIEW")


def _optional_bool(cell: Optional[ResolvedCellValue]) -> Optional[bool]:
    if cell is None or cell.normalized_value is None:
        return None
    return cell.normalized_value if type(cell.normalized_value) is bool else None


def _governed_bool(cell: Optional[ResolvedCellValue]) -> bool:
    if cell is None or type(cell.normalized_value) is not bool or cell.value_cell is None:
        return False
    validation = cell.value_cell.data_validation
    return validation is not None and validation.condition.condition_type == "BOOLEAN"


def _links(cell: Optional[ResolvedCellValue]) -> Tuple[Tuple[str, ...], bool]:
    if cell is None or cell.value_cell is None:
        return (), False
    source = cell.value_cell
    raw = []
    for run in source.text_format_runs:
        if run.link is not None:
            raw.append(run.link.uri)
    if source.hyperlink is not None:
        raw.append(source.hyperlink)
    entered = source.user_entered_value
    formula = entered.formula_value if entered is not None else None
    if formula is not None:
        try:
            formula_url = _parse_hyperlink_first_argument(formula)
        except LinkExtractionError:
            return (), True
        if formula_url is not None:
            raw.append(formula_url)
    display = cell.display_value
    if type(display) is str and _LITERAL_HTTP_URL.fullmatch(display):
        raw.append(display)
    canonical = []
    unsafe = False
    for raw_url in raw:
        try:
            value = validate_and_canonicalize_evidence_url(raw_url).value
        except URLValidationError:
            unsafe = True
            continue
        if value not in canonical:
            canonical.append(value)
    return tuple(canonical), unsafe


def _lineage(spec, row_index, resolved, envelope) -> CanonicalSourceLineage:
    columns = {}
    ranges = {}
    for field_name, cell in resolved.items():
        if cell is None:
            continue
        column = _column_letter(cell.field_contract.source_column_index)
        columns[field_name] = column
        merge = cell.field_lineage.merge_range
        if merge is None:
            ranges[field_name] = f"{column}{row_index + 1}"
        else:
            ranges[field_name] = (
                f"{_column_letter(merge.start_column_index)}{merge.start_row_index + 1}:"
                f"{_column_letter(merge.end_column_index - 1)}{merge.end_row_index}"
            )
    return CanonicalSourceLineage(
        spreadsheet_id_hash=envelope.target_identity_hash,
        sheet_id=spec.sheet_id,
        sheet_title=spec.title,
        source_row=row_index + 1,
        source_columns=dict(columns),
        source_ranges=dict(ranges),
        source_fingerprint=envelope.source_fingerprint,
        sync_batch_id=envelope.correlation_id,
    )


def _clone_lineage(value: CanonicalSourceLineage) -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash=value.spreadsheet_id_hash,
        sheet_id=value.sheet_id,
        sheet_title=value.sheet_title,
        source_row=value.source_row,
        source_columns=dict(value.source_columns),
        source_ranges=dict(value.source_ranges),
        source_fingerprint=value.source_fingerprint,
        sync_batch_id=value.sync_batch_id,
    )


def _review(source_ref, reason_codes, builder) -> SourceReviewFact:
    return builder.new(
        SourceReviewFact,
        source_ref=source_ref,
        reason_codes=tuple(sorted(set(reason_codes))),
    )


def _id_diagnostics(builder) -> Tuple[IdDiagnostic, ...]:
    definitions = (
        (IdNamespace.MREC, "merchant_case", "MREC", "MERCHANT_MREC_ASSIGNMENT_DEFERRED"),
        (IdNamespace.BRD, "merchant_case", "BRD", "MERCHANT_BRD_ASSIGNMENT_DEFERRED"),
        (IdNamespace.NONE, "merchant_case", "ID Review Status", "MERCHANT_ID_REVIEW_STATUS_DEFERRED"),
        (IdNamespace.MET, "public_metric", "MET", "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED"),
        (IdNamespace.BRD, "handle_mapping", "品牌 ID 對照", "BRAND_ID_MAPPING_AUTHORITY_DEFERRED"),
        (IdNamespace.BRD, "handle_mapping", "品牌 ID 初始化審核", "BRAND_ID_INITIAL_REVIEW_DEFERRED"),
    )
    return tuple(
        builder.new(
            IdDiagnostic,
            namespace=namespace,
            source_class=source_class,
            field_name_or_surface=surface,
            reason_code=reason,
        )
        for namespace, source_class, surface, reason in definitions
    )


def _build_normalization_entrypoint():
    trusted_objects = {}
    trusted_builders = {}
    source_ref_cache = {}
    project_candidates = _project_brand_review_candidates
    source_ref_digest = _safe_source_ref_digest
    candidate_ref_digest = _candidate_ref_digest
    website_ref = _website_ref
    safe_hostname = _safe_hostname
    safe_handle_value = _safe_handle_value
    validate_evidence_url = validate_and_canonicalize_evidence_url
    specs = dict(_SPECS)
    validated_context_envelope = _validated_context_envelope
    context_result = _context_result
    merchant_rows = _merchant_rows
    restricted_rows_normalizer = _restricted_rows
    public_metric_rows = _public_metric_rows
    pending_rows_normalizer = _pending_rows
    handle_mapping_rows = _handle_mapping_rows
    id_diagnostics = _id_diagnostics

    source_class_by_type = {
        MerchantCaseStaging: "merchant_case",
        RestrictedDenylistStaging: "restricted_customer",
        RestrictedGovernanceFact: "restricted_customer",
        PublicMetricStaging: "public_metric",
        PendingMetricStaging: "pending_metric",
        PendingMetricGovernanceFact: "pending_metric",
        HandleMappingStaging: "handle_mapping",
    }
    collection_contracts = {
        "merchant_cases": (MerchantCaseStaging, "merchant_case"),
        "restricted_denylist": (RestrictedDenylistStaging, "restricted_customer"),
        "restricted_facts": (RestrictedGovernanceFact, "restricted_customer"),
        "public_metrics": (PublicMetricStaging, "public_metric"),
        "pending_metrics": (PendingMetricStaging, "pending_metric"),
        "pending_facts": (PendingMetricGovernanceFact, "pending_metric"),
        "handle_mappings": (HandleMappingStaging, "handle_mapping"),
        "brand_review_candidates": (BrandReviewCandidate, None),
        "duplicate_review_facts": (DuplicateMerchantReviewFact, "merchant_case"),
        "review_facts": (SourceReviewFact, None),
        "id_diagnostics": (IdDiagnostic, None),
    }

    def remember(value, record):
        identity = id(value)

        def discard(reference):
            current = trusted_objects.get(identity)
            if current is not None and current[0] is reference:
                trusted_objects.pop(identity, None)

        reference = weakref.ref(value, discard)
        trusted_objects[identity] = (reference, record)

    def trusted_record(value):
        current = trusted_objects.get(id(value))
        if current is None or current[0]() is not value:
            return None
        return current[1]

    def builder_binding(builder):
        current = trusted_builders.get(id(builder))
        if current is None or current[0]() is not builder:
            _fail("WP3_PIPELINE_BUILDER_INVALID")
        return current[1]

    def validate_lineage(value, binding, source_class, source_row):
        spec = specs[source_class]
        if (
            type(value) is not CanonicalSourceLineage
            or value.spreadsheet_id_hash != binding.target_identity_hash
            or value.source_fingerprint != binding.source_fingerprint
            or value.sync_batch_id != binding.sync_batch_id
            or value.sheet_id != spec.sheet_id
            or value.sheet_title != spec.title
            or value.source_row != source_row
        ):
            _fail("WP3_SOURCE_LINEAGE_BINDING_INVALID")

    def validate_source_ref(value, binding, source_class=None):
        record = trusted_record(value)
        if (
            type(value) is not SafeSourceRef
            or record is None
            or record.kind != "safe_source_ref"
            or record.binding != binding
            or (source_class is not None and record.source_class != source_class)
            or value.source_class != record.source_class
            or value.sheet_id != record.sheet_id
            or value.source_row != record.source_row
        ):
            _fail("WP3_SAFE_SOURCE_REF_BINDING_INVALID")
        expected = source_ref_digest(
            record.source_class,
            record.sheet_id,
            record.source_row,
            binding.target_identity_hash,
            binding.source_fingerprint,
        )
        if value.source_ref != expected:
            _fail("WP3_SAFE_SOURCE_REF_BINDING_INVALID")
        return record

    def validate_nested(value, binding, expected_type, source_class=None):
        record = trusted_record(value)
        if (
            type(value) is not expected_type
            or record is None
            or record.binding != binding
            or (source_class is not None and record.source_class != source_class)
        ):
            _fail("WP3_NESTED_CONTEXT_BINDING_INVALID")
        return record

    class PipelineBuilder:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise TypeError("WP3_PIPELINE_BUILDER_CONSTRUCTION_FORBIDDEN")

        def source_ref(self, spec, row_index):
            binding = builder_binding(self)
            if (
                type(spec) is not WP3SheetSpec
                or specs.get(spec.source_class) is not spec
                or type(row_index) is not int
                or not spec.first_data_row - 1 <= row_index <= spec.last_data_row - 1
            ):
                _fail("WP3_SAFE_SOURCE_REF_SOURCE_INVALID")
            cache_key = (
                binding.context_identity,
                binding.result_identity,
                binding.envelope_identity,
                spec.source_class,
                row_index,
            )
            cached = source_ref_cache.get(cache_key)
            if cached is not None and cached() is not None:
                return cached()
            value = object.__new__(SafeSourceRef)
            object.__setattr__(value, "_source_class", spec.source_class)
            object.__setattr__(value, "_sheet_id", spec.sheet_id)
            object.__setattr__(value, "_source_row", row_index + 1)
            object.__setattr__(
                value,
                "_source_ref",
                source_ref_digest(
                    spec.source_class,
                    spec.sheet_id,
                    row_index + 1,
                    binding.target_identity_hash,
                    binding.source_fingerprint,
                ),
            )
            remember(
                value,
                _TrustedObjectRecord(
                    "safe_source_ref",
                    binding,
                    spec.source_class,
                    spec.sheet_id,
                    row_index + 1,
                ),
            )
            source_ref_cache[cache_key] = weakref.ref(value)
            return value

        def new(self, cls, **fields):
            binding = builder_binding(self)
            if cls not in {
                AssetCellStaging,
                MerchantCaseStaging,
                RestrictedDenylistStaging,
                PublicMetricStaging,
                PendingMetricStaging,
                HandleMappingStaging,
                RestrictedGovernanceFact,
                PendingMetricGovernanceFact,
                SourceReviewFact,
                DuplicateMerchantReviewFact,
                IdDiagnostic,
            }:
                _fail("WP3_TRUSTED_OUTPUT_TYPE_INVALID")
            expected_fields = {
                name[1:] for name in cls.__slots__ if name != "__weakref__"
            }
            if set(fields) != expected_fields:
                _fail("WP3_TRUSTED_OUTPUT_FIELDS_INVALID")
            normalized_fields = {
                name: tuple(value) if isinstance(value, list) else value
                for name, value in fields.items()
            }
            source_class = source_class_by_type.get(cls)
            sheet_id = None
            source_row = None
            source_ref = normalized_fields.get("source_ref")
            if source_ref is not None:
                source_record = validate_source_ref(source_ref, binding, source_class)
                source_class = source_record.source_class
                sheet_id = source_record.sheet_id
                source_row = source_record.source_row
            lineage = normalized_fields.get("lineage")
            if cls is AssetCellStaging:
                source_class = "merchant_case"
                if normalized_fields["field_name"] not in {"article", "video", "podcast", "news"}:
                    _fail("WP3_ASSET_FIELD_INVALID")
                if type(lineage) is not CanonicalSourceLineage:
                    _fail("WP3_SOURCE_LINEAGE_BINDING_INVALID")
                sheet_id = lineage.sheet_id
                source_row = lineage.source_row
            if lineage is not None:
                validate_lineage(lineage, binding, source_class, source_row)
            if cls is MerchantCaseStaging:
                for asset in normalized_fields["asset_cells"]:
                    asset_record = validate_nested(
                        asset, binding, AssetCellStaging, "merchant_case"
                    )
                    if (
                        asset_record.sheet_id != sheet_id
                        or asset_record.source_row != source_row
                    ):
                        _fail("WP3_NESTED_CONTEXT_BINDING_INVALID")
                handle = normalized_fields["normalized_handle"]
                if handle is not None and not safe_handle_value(handle):
                    _fail("WP3_SAFE_HANDLE_BINDING_INVALID")
            if cls is HandleMappingStaging:
                handle = normalized_fields["normalized_handle"]
                if handle is not None and not safe_handle_value(handle):
                    _fail("WP3_SAFE_HANDLE_BINDING_INVALID")
            if cls is DuplicateMerchantReviewFact:
                refs = normalized_fields["source_refs"]
                if type(refs) is not tuple or len(refs) < 2:
                    _fail("WP3_DUPLICATE_REVIEW_BINDING_INVALID")
                records = [
                    validate_source_ref(ref, binding, "merchant_case") for ref in refs
                ]
                source_class = "merchant_case"
                sheet_id = specs[source_class].sheet_id
                source_row = None
                if normalized_fields["reason_code"] != "DUPLICATE_MERCHANT_INTERVIEW_REVIEW":
                    _fail("WP3_DUPLICATE_REVIEW_BINDING_INVALID")
                source_ref_ids = tuple(id(ref) for ref in refs)
            else:
                source_ref_ids = (() if source_ref is None else (id(source_ref),))
            if cls is IdDiagnostic:
                source_class = normalized_fields["source_class"]
            value = object.__new__(cls)
            for name, field_value in normalized_fields.items():
                object.__setattr__(value, f"_{name}", field_value)
            remember(
                value,
                _TrustedObjectRecord(
                    cls.__name__,
                    binding,
                    source_class,
                    sheet_id,
                    source_row,
                    source_ref_ids,
                ),
            )
            return value

        def brand_candidates(self, evidence):
            binding = builder_binding(self)
            records = tuple(evidence)
            evidence_ref_ids = []
            for item in records:
                if type(item) is not _UntrustedBrandEvidence:
                    _fail("WP3_BRAND_EVIDENCE_TYPE_INVALID")
                source_record = validate_source_ref(item.source_ref, binding)
                if source_record.source_class not in {"merchant_case", "handle_mapping"}:
                    _fail("WP3_BRAND_EVIDENCE_SOURCE_INVALID")
                if item.handle_mapping is not (
                    source_record.source_class == "handle_mapping"
                ):
                    _fail("WP3_BRAND_EVIDENCE_SOURCE_INVALID")
                if item.normalized_handle is not None and not safe_handle_value(
                    item.normalized_handle
                ):
                    _fail("WP3_SAFE_HANDLE_BINDING_INVALID")
                for canonical_url in item.canonical_urls:
                    try:
                        validated = validate_evidence_url(canonical_url).value
                    except URLValidationError:
                        _fail("WP3_BRAND_EVIDENCE_URL_INVALID")
                    if validated != canonical_url:
                        _fail("WP3_BRAND_EVIDENCE_URL_INVALID")
                evidence_ref_ids.append(id(item.source_ref))
            projections = project_candidates(records)
            projected_ref_ids = [
                id(source_ref)
                for projection in projections
                for source_ref in projection.source_refs
            ]
            if sorted(projected_ref_ids) != sorted(evidence_ref_ids):
                _fail("WP3_BRAND_PROJECTION_SOURCE_BINDING_INVALID")
            candidates = []
            for projection in projections:
                if type(projection) is not _UntrustedBrandCandidateProjection:
                    _fail("WP3_BRAND_PROJECTION_TYPE_INVALID")
                sources = projection.source_refs
                for source_ref in sources:
                    validate_source_ref(source_ref, binding)
                if any(not safe_handle_value(handle) for handle in projection.handles):
                    _fail("WP3_SAFE_HANDLE_BINDING_INVALID")
                website_refs = tuple(
                    website_ref(url) for url in projection.canonical_urls
                )
                hosts = tuple(
                    sorted({safe_hostname(url) for url in projection.canonical_urls})
                )
                candidate_ref = candidate_ref_digest(
                    projection.classification,
                    projection.handles,
                    sources,
                    website_refs,
                )
                candidate = object.__new__(BrandReviewCandidate)
                object.__setattr__(candidate, "_candidate_ref", candidate_ref)
                object.__setattr__(
                    candidate, "_classification", projection.classification
                )
                object.__setattr__(candidate, "_source_refs", sources)
                object.__setattr__(
                    candidate,
                    "_normalized_handle",
                    projection.handles[0] if len(projection.handles) == 1 else None,
                )
                object.__setattr__(candidate, "_website_hosts", hosts)
                object.__setattr__(candidate, "_website_refs", website_refs)
                object.__setattr__(
                    candidate, "_reason_codes", projection.reason_codes
                )
                remember(
                    candidate,
                    _TrustedObjectRecord(
                        "BrandReviewCandidate",
                        binding,
                        source_ref_identities=tuple(id(ref) for ref in sources),
                    ),
                )
                candidates.append(candidate)
            return tuple(sorted(candidates, key=lambda item: item.candidate_ref))

        def batch(self, **fields):
            binding = builder_binding(self)
            expected_fields = {
                name[1:]
                for name in CanonicalNormalizationBatch.__slots__
                if name != "__weakref__"
            }
            if set(fields) != expected_fields:
                _fail("WP3_BATCH_FIELDS_INVALID")
            normalized_fields = {
                name: tuple(value) if isinstance(value, list) else value
                for name, value in fields.items()
            }
            for name, (expected_type, source_class) in collection_contracts.items():
                values = normalized_fields[name]
                if type(values) is not tuple:
                    _fail("WP3_BATCH_COLLECTION_TYPE_INVALID")
                for value in values:
                    validate_nested(value, binding, expected_type, source_class)
            exclusions = normalized_fields["excluded_public_metrics"]
            if type(exclusions) is not tuple or any(
                type(value) is not ExcludedSourceRef
                or value.sheet_id != specs["public_metric"].sheet_id
                or not specs["public_metric"].first_data_row
                <= value.source_row
                <= specs["public_metric"].last_data_row
                for value in exclusions
            ):
                _fail("WP3_EXCLUDED_SOURCE_BINDING_INVALID")
            restricted_rows = {
                (item.source_ref.sheet_id, item.source_ref.source_row)
                for item in normalized_fields["restricted_denylist"]
            }
            if restricted_rows != {
                (item.sheet_id, item.source_row)
                for item in normalized_fields["restricted_facts"]
            }:
                _fail("WP3_RESTRICTED_FACT_BINDING_INVALID")
            pending_rows = {
                (item.source_ref.sheet_id, item.source_ref.source_row)
                for item in normalized_fields["pending_metrics"]
            }
            if pending_rows != {
                (item.sheet_id, item.source_row)
                for item in normalized_fields["pending_facts"]
            }:
                _fail("WP3_PENDING_FACT_BINDING_INVALID")
            value = object.__new__(CanonicalNormalizationBatch)
            for name, field_value in normalized_fields.items():
                object.__setattr__(value, f"_{name}", field_value)
            remember(
                value,
                _TrustedObjectRecord("CanonicalNormalizationBatch", binding),
            )
            return value

    def normalize(context: CoverageProvenBatchContext) -> CanonicalNormalizationBatch:
        """Normalize one exact, authentic, structurally valid synthetic WP2 batch."""

        if type(context) is not CoverageProvenBatchContext:
            _fail("WP3_COVERAGE_PROVEN_CONTEXT_REQUIRED")
        envelope = validated_context_envelope(context)
        if envelope is None:
            _fail("WP3_COVERAGE_PROVEN_CONTEXT_INVALID")
        if (
            envelope.run_mode is not RunMode.SYNTHETIC
            or envelope.disposition
            is not SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE
            or envelope.structural_reason_codes
        ):
            _fail("WP3_TRUSTED_SYNTHETIC_CONTEXT_REQUIRED")
        result = context_result(context)
        if (
            validated_context_envelope(context) is not envelope
            or context_result(context) is not result
        ):
            _fail("WP3_COVERAGE_PROVEN_CONTEXT_INVALID")
        binding = _ContextBinding(
            context,
            result,
            envelope,
            envelope.target_identity_hash,
            envelope.source_fingerprint,
            envelope.correlation_id,
        )
        builder = object.__new__(PipelineBuilder)
        builder_identity = id(builder)

        def discard_builder(reference):
            current = trusted_builders.get(builder_identity)
            if current is not None and current[0] is reference:
                trusted_builders.pop(builder_identity, None)

        trusted_builders[builder_identity] = (
            weakref.ref(builder, discard_builder),
            binding,
        )
        snapshot = result.snapshot
        merchants, merchant_evidence, duplicates, merchant_reviews = merchant_rows(
            snapshot, envelope, builder
        )
        restricted, restricted_facts, restricted_reviews = restricted_rows_normalizer(
            snapshot, envelope, builder
        )
        public_metrics, exclusions, public_reviews = public_metric_rows(
            snapshot, envelope, builder
        )
        pending, pending_facts, pending_reviews = pending_rows_normalizer(
            snapshot, envelope, builder
        )
        mappings, mapping_evidence, mapping_reviews = handle_mapping_rows(
            snapshot, envelope, builder
        )
        candidates = builder.brand_candidates(
            (*merchant_evidence, *mapping_evidence)
        )
        return builder.batch(
            merchant_cases=merchants,
            restricted_denylist=restricted,
            restricted_facts=restricted_facts,
            public_metrics=public_metrics,
            excluded_public_metrics=exclusions,
            pending_metrics=pending,
            pending_facts=pending_facts,
            handle_mappings=mappings,
            brand_review_candidates=candidates,
            duplicate_review_facts=duplicates,
            review_facts=tuple(
                sorted(
                    (
                        *merchant_reviews,
                        *restricted_reviews,
                        *public_reviews,
                        *pending_reviews,
                        *mapping_reviews,
                    ),
                    key=lambda item: (
                        item.source_ref.source_ref,
                        item.reason_codes,
                    ),
                )
            ),
            id_diagnostics=id_diagnostics(builder),
        )

    return normalize


normalize_coverage_proven_batch = _build_normalization_entrypoint()
del _build_normalization_entrypoint


def _sheet(snapshot: SpreadsheetSnapshot, sheet_id: int) -> SheetSnapshot:
    for sheet in snapshot.sheets:
        if sheet.sheet_id == sheet_id:
            return sheet
    _fail("WP3_SOURCE_SHEET_MISSING")


def _column_index(column: str) -> int:
    return ord(column) - ord("A")


def _column_letter(index: int) -> str:
    if type(index) is not int or index < 0:
        _fail("WP3_COLUMN_INDEX_INVALID")
    value = index + 1
    label = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _fail(code: str) -> None:
    raise CanonicalNormalizationError(code) from None


__all__ = [
    "AssetCellStaging",
    "CandidateReviewState",
    "CanonicalNormalizationBatch",
    "CanonicalNormalizationError",
    "DuplicateMerchantReviewFact",
    "HandleMappingStaging",
    "ID_DIAGNOSTIC_SCHEMA_VERSION",
    "IdDiagnostic",
    "IdNamespace",
    "IdentityState",
    "MerchantCaseStaging",
    "PendingMetricGovernanceFact",
    "PendingMetricStaging",
    "PublicMetricStaging",
    "RestrictedDenylistStaging",
    "RestrictedGovernanceFact",
    "SourceReviewFact",
    "WP3FieldSpec",
    "WP3SheetSpec",
    "WP3_FIELD_REGISTRY",
    "normalize_coverage_proven_batch",
]
