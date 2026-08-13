"""Offline Sprint 1 WP3 canonical normalization and governance staging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
import re
import unicodedata
from typing import Dict, Iterable, Optional, Tuple

from .brand_review_candidates import (
    BrandReviewCandidate,
    SafeSourceRef,
    _build_brand_review_candidates,
    _new_brand_evidence,
    _new_safe_source_ref,
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
    ValueSource,
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


class _WP3ConstructionProvenance:
    __slots__ = ("_authority", "_context_identity", "_result_identity", "_envelope_identity")

    def __new__(cls, *args: object, **kwargs: object) -> "_WP3ConstructionProvenance":
        raise TypeError("WP3_CONSTRUCTION_PROVENANCE_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WP3_CONSTRUCTION_PROVENANCE_IMMUTABLE")

    def __repr__(self) -> str:
        return "_WP3ConstructionProvenance(<opaque>)"

    def __reduce__(self) -> object:
        raise TypeError("WP3_CONSTRUCTION_PROVENANCE_PICKLE_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("WP3_CONSTRUCTION_PROVENANCE_PICKLE_FORBIDDEN")


def _build_construction_authority():
    authority = object()

    def issue(context, result, envelope):
        if (
            type(context) is not CoverageProvenBatchContext
            or _validated_context_envelope(context) is not envelope
            or _context_result(context) is not result
        ):
            _fail("WP3_CONSTRUCTION_PROVENANCE_ISSUANCE_INVALID")
        provenance = object.__new__(_WP3ConstructionProvenance)
        object.__setattr__(provenance, "_authority", authority)
        object.__setattr__(provenance, "_context_identity", id(context))
        object.__setattr__(provenance, "_result_identity", id(result))
        object.__setattr__(provenance, "_envelope_identity", id(envelope))
        return provenance

    def new(cls, provenance, **fields):
        if type(provenance) is not _WP3ConstructionProvenance:
            _fail("WP3_CONSTRUCTION_PROVENANCE_INVALID")
        try:
            trusted = object.__getattribute__(provenance, "_authority") is authority
        except (AttributeError, TypeError):
            trusted = False
        if not trusted:
            _fail("WP3_CONSTRUCTION_PROVENANCE_INVALID")
        value = object.__new__(cls)
        object.__setattr__(value, "_provenance", provenance)
        for name, field_value in fields.items():
            if isinstance(field_value, list):
                field_value = tuple(field_value)
            object.__setattr__(value, f"_{name}", field_value)
        return value

    return issue, new


_issue_wp3_provenance, _new = _build_construction_authority()
del _build_construction_authority


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
    required: bool = False
    merge_inheritance_allowed: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class WP3SheetSpec:
    source_class: str
    sheet_id: int
    title: str
    hidden: bool
    header_row: Optional[int]
    first_data_row: int
    last_data_row: int
    first_column: str
    last_column: str
    fields: Tuple[WP3FieldSpec, ...]


WP3_FIELD_REGISTRY = (
    WP3SheetSpec(
        "merchant_case", 0, "商家/夥伴案例資料庫", False, 6, 7, 1018, "A", "L",
        (
            WP3FieldSpec("interview_year", "A", "YEAR_ONLY"),
            WP3FieldSpec("source_status", "B", "TEXT"),
            WP3FieldSpec("merchant_name", "C", "TEXT_LINK", True, sensitive=True),
            WP3FieldSpec("normalized_handle", "D", "HANDLE"),
            WP3FieldSpec("sales_category_lv1", "E", "TEXT"),
            WP3FieldSpec("sales_category_lv2", "F", "TEXT"),
            WP3FieldSpec("content_tags", "G", "TAGS"),
            WP3FieldSpec("article", "H", "TEXT_LINK"),
            WP3FieldSpec("video", "I", "TEXT_LINK"),
            WP3FieldSpec("podcast", "J", "TEXT_LINK"),
            WP3FieldSpec("news", "K", "TEXT_LINK"),
            WP3FieldSpec("notes", "L", "TEXT", sensitive=True),
        ),
    ),
    WP3SheetSpec(
        "restricted_customer", 1456785208, "「不可公開」客戶名單", False, 4, 5, 994, "A", "H",
        (
            WP3FieldSpec("updated_year", "A", "YEAR_ONLY"),
            WP3FieldSpec("customer_brand", "B", "TEXT", sensitive=True),
            WP3FieldSpec("website", "C", "TEXT_LINK", sensitive=True),
            WP3FieldSpec("sales_category_lv1", "D", "TEXT"),
            WP3FieldSpec("nda_signed", "E", "BOOLEAN"),
            WP3FieldSpec("nda_uploaded", "F", "BOOLEAN"),
            WP3FieldSpec("restricted_reason", "G", "TEXT", sensitive=True),
            WP3FieldSpec("submitted_by", "H", "TEXT", sensitive=True),
        ),
    ),
    WP3SheetSpec(
        "public_metric", 918878896, "「可公開」對外數據", False, 6, 7, 999, "A", "M",
        (
            WP3FieldSpec("metric_type", "A", "TEXT", True, True),
            WP3FieldSpec("indicator", "B", "TEXT", True, True),
            WP3FieldSpec("statement", "C", "TEXT", True, sensitive=True),
            WP3FieldSpec("note", "D", "TEXT", sensitive=True),
            WP3FieldSpec("updated_date", "E", "DATE_ONLY"),
            WP3FieldSpec("evidence_url", "F", "TEXT_LINK", merge_inheritance_allowed=True),
            WP3FieldSpec("press_release", "G", "BOOLEAN", True),
            WP3FieldSpec("owned_media", "H", "BOOLEAN", True),
            WP3FieldSpec("saleskits", "I", "BOOLEAN", True),
            WP3FieldSpec("verbal_briefing", "J", "BOOLEAN", True),
            WP3FieldSpec("speaking_deck", "K", "BOOLEAN", True),
            WP3FieldSpec("website_recruiting", "L", "BOOLEAN", True),
            WP3FieldSpec("ads", "M", "BOOLEAN", True),
        ),
    ),
    WP3SheetSpec(
        "pending_metric", 956677822, "待確認數據", True, None, 3, 999, "A", "D",
        (
            WP3FieldSpec("metric_type", "A", "TEXT"),
            WP3FieldSpec("indicator", "B", "TEXT"),
            WP3FieldSpec("statement", "C", "TEXT", True, sensitive=True),
            WP3FieldSpec("note", "D", "TEXT", sensitive=True),
        ),
    ),
    WP3SheetSpec(
        "handle_mapping", 737692182, "handle 比對", True, 1, 2, 998, "A", "D",
        (
            WP3FieldSpec("normalized_handle", "A", "HANDLE"),
            WP3FieldSpec("name_with_link", "B", "TEXT_LINK", sensitive=True),
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
    __slots__ = ("_provenance",)

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
    __slots__ = ("_provenance",)

    def __new__(cls, *args: object, **kwargs: object) -> "_SafeFact":
        raise TypeError(f"{cls.__name__.upper()}_CONSTRUCTION_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WP3_SAFE_FACT_IMMUTABLE")


class RestrictedGovernanceFact(_SafeFact):
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


def normalize_coverage_proven_batch(
    context: CoverageProvenBatchContext,
) -> CanonicalNormalizationBatch:
    """Normalize one exact, authentic, structurally valid synthetic WP2 batch."""

    if type(context) is not CoverageProvenBatchContext:
        _fail("WP3_COVERAGE_PROVEN_CONTEXT_REQUIRED")
    envelope = _validated_context_envelope(context)
    if envelope is None:
        _fail("WP3_COVERAGE_PROVEN_CONTEXT_INVALID")
    if (
        envelope.run_mode is not RunMode.SYNTHETIC
        or envelope.disposition is not SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE
        or envelope.structural_reason_codes
    ):
        _fail("WP3_TRUSTED_SYNTHETIC_CONTEXT_REQUIRED")
    result = _context_result(context)
    snapshot = result.snapshot
    provenance = _issue_wp3_provenance(context, result, envelope)

    merchants, merchant_evidence, duplicates, merchant_reviews = _merchant_rows(
        snapshot, envelope, provenance
    )
    restricted, restricted_facts, restricted_reviews = _restricted_rows(
        snapshot, envelope, provenance
    )
    public_metrics, exclusions, public_reviews = _public_metric_rows(
        snapshot, envelope, provenance
    )
    pending, pending_facts, pending_reviews = _pending_rows(
        snapshot, envelope, provenance
    )
    mappings, mapping_evidence, mapping_reviews = _handle_mapping_rows(
        snapshot, envelope, provenance
    )
    candidates = _build_brand_review_candidates(
        (*merchant_evidence, *mapping_evidence)
    )
    batch = _new(
        CanonicalNormalizationBatch,
        provenance,
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
                (*merchant_reviews, *restricted_reviews, *public_reviews, *pending_reviews, *mapping_reviews),
                key=lambda item: (item.source_ref.source_ref, item.reason_codes),
            )
        ),
        id_diagnostics=_id_diagnostics(provenance),
    )
    return batch


def _merchant_rows(snapshot, envelope, provenance):
    spec = _SPECS["merchant_case"]
    staging = []
    evidence = []
    reviews = []
    duplicate_keys: Dict[tuple, list] = {}
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance)
        source_ref = _source_ref(spec, row_index, envelope)
        name = _text(resolved.get("merchant_name"))
        handle = _handle(resolved.get("normalized_handle"))
        year = _year_value(resolved.get("interview_year"), reviews, source_ref, provenance)
        name_urls, name_unsafe = _links(resolved.get("merchant_name"))
        if name is None:
            reviews.append(_review(source_ref, ("MERCHANT_NAME_MISSING",), provenance))
        assets = []
        duplicate_asset_values = []
        for field_name in ("article", "video", "podcast", "news"):
            cell = resolved.get(field_name)
            urls, unsafe = _links(cell)
            text = _text(cell)
            lineage = _lineage(spec, row_index, {field_name: cell}, envelope)
            assets.append(
                _new(
                    AssetCellStaging, provenance, field_name=field_name, text=text,
                    canonical_urls=urls, unsafe_url=unsafe, lineage=lineage,
                )
            )
            duplicate_asset_values.append((text, urls))
        lineage = _lineage(spec, row_index, resolved, envelope)
        item = _new(
            MerchantCaseStaging,
            provenance,
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
            _new_brand_evidence(
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
                _new(
                    DuplicateMerchantReviewFact,
                    provenance,
                    source_refs=tuple(sorted(refs, key=lambda item: item.source_ref)),
                    reason_code="DUPLICATE_MERCHANT_INTERVIEW_REVIEW",
                )
            )
    return tuple(staging), tuple(evidence), tuple(duplicate_facts), tuple(reviews)


def _restricted_rows(snapshot, envelope, provenance):
    spec = _SPECS["restricted_customer"]
    staging = []
    facts = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance)
        source_ref = _source_ref(spec, row_index, envelope)
        brand = _text(resolved.get("customer_brand"))
        website = _text(resolved.get("website"))
        urls, _unsafe = _links(resolved.get("website"))
        reason_codes = ["RESTRICTED_SOURCE"]
        if brand is None:
            reason_codes.append("RESTRICTED_IDENTITY_MISSING")
        lineage = _lineage(spec, row_index, resolved, envelope)
        staging.append(
            _new(
                RestrictedDenylistStaging,
                provenance,
                source_ref=source_ref,
                lineage=lineage,
                updated_year=_year_value(resolved.get("updated_year"), reviews, source_ref, provenance),
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
            _new(
                RestrictedGovernanceFact,
                provenance,
                source_ref=source_ref,
                reason_codes=tuple(sorted(reason_codes)),
                identity_term_count=int(brand is not None)
                + int(website is not None or bool(urls)),
            )
        )
    return tuple(staging), tuple(facts), tuple(reviews)


def _public_metric_rows(snapshot, envelope, provenance):
    spec = _SPECS["public_metric"]
    staging = []
    exclusions = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        review_count_before_resolution = len(reviews)
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance)
        source_normalization_failed = len(reviews) != review_count_before_resolution
        source_ref = _source_ref(spec, row_index, envelope)
        statement = _text(resolved.get("statement"))
        if statement is None:
            reviews.append(_review(source_ref, ("PUBLIC_METRIC_STATEMENT_MISSING",), provenance))
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
                    _new(
                        PublicMetricStaging,
                        provenance,
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
                reviews.append(_review(source_ref, codes, provenance))
        else:
            if type(minimized) is not ExcludedSourceRef:
                _fail("WP3_FINAL_METRIC_OBJECT_FORBIDDEN")
            exclusions.append(minimized)
    return tuple(staging), tuple(exclusions), tuple(reviews)


def _pending_rows(snapshot, envelope, provenance):
    spec = _SPECS["pending_metric"]
    staging = []
    facts = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance)
        source_ref = _source_ref(spec, row_index, envelope)
        statement = _text(resolved.get("statement"))
        if statement is None:
            reviews.append(_review(source_ref, ("PENDING_METRIC_STATEMENT_MISSING",), provenance))
        staging.append(
            _new(
                PendingMetricStaging,
                provenance,
                source_ref=source_ref,
                lineage=_lineage(spec, row_index, resolved, envelope),
                metric_type=_text(resolved.get("metric_type")),
                indicator=_text(resolved.get("indicator")),
                statement=statement,
                note=_text(resolved.get("note")),
            )
        )
        facts.append(_new(PendingMetricGovernanceFact, provenance, source_ref=source_ref))
    return tuple(staging), tuple(facts), tuple(reviews)


def _handle_mapping_rows(snapshot, envelope, provenance):
    spec = _SPECS["handle_mapping"]
    staging = []
    evidence = []
    reviews = []
    for row_index in _occupied_rows(snapshot, spec):
        resolved = _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance)
        source_ref = _source_ref(spec, row_index, envelope)
        handle = _handle(resolved.get("normalized_handle"))
        name = _text(resolved.get("name_with_link"))
        urls, unsafe = _links(resolved.get("name_with_link"))
        if handle is None and not urls:
            reviews.append(_review(source_ref, ("HANDLE_MAPPING_IDENTITY_EVIDENCE_MISSING",), provenance))
        staging.append(
            _new(
                HandleMappingStaging,
                provenance,
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
            _new_brand_evidence(
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


def _resolve_fields(snapshot, envelope, spec, row_index, reviews, provenance):
    resolved = {}
    source_ref = _source_ref(spec, row_index, envelope)
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
            reviews.append(_review(source_ref, (exc.code,), provenance))
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
    value = _text(cell)
    return value.casefold() if value is not None else None


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


def _year_value(cell, reviews, source_ref, provenance) -> Optional[int]:
    text = _text(cell)
    if text is None:
        return None
    if _YEAR.fullmatch(text) and 1000 <= int(text) <= 9999:
        return int(text)
    reviews.append(_review(source_ref, ("YEAR_ONLY_NEEDS_REVIEW",), provenance))
    return None


def _date_value(cell: Optional[ResolvedCellValue]) -> Optional[date]:
    if cell is None or cell.normalized_value is None:
        return None
    display = cell.display_value
    if cell.value_source is ValueSource.FORMATTED_VALUE and type(display) is str:
        normalized = unicodedata.normalize("NFKC", display).strip()
        matched = _DATE_TEXT.fullmatch(normalized)
        if matched is None:
            _fail("DATE_ONLY_NEEDS_REVIEW")
        try:
            return date(int(matched.group(1)), int(matched.group(3)), int(matched.group(4)))
        except ValueError:
            _fail("DATE_ONLY_NEEDS_REVIEW")
    value_cell = cell.value_cell
    effective = value_cell.effective_value if value_cell is not None else None
    number = effective.number_value if effective is not None else None
    text = effective.string_value if effective is not None else None
    if type(text) is str:
        normalized = unicodedata.normalize("NFKC", text).strip()
        matched = _DATE_TEXT.fullmatch(normalized)
        if matched is None:
            _fail("DATE_ONLY_NEEDS_REVIEW")
        try:
            return date(int(matched.group(1)), int(matched.group(3)), int(matched.group(4)))
        except ValueError:
            _fail("DATE_ONLY_NEEDS_REVIEW")
    if type(number) is bool or not isinstance(number, (int, float)):
        _fail("DATE_ONLY_NEEDS_REVIEW")
    if number < 0 or int(number) != number:
        _fail("DATE_ONLY_NEEDS_REVIEW")
    try:
        return _GOOGLE_DATE_EPOCH + timedelta(days=int(number))
    except (OverflowError, ValueError):
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


def _source_ref(spec, row_index, envelope) -> SafeSourceRef:
    return _new_safe_source_ref(
        source_class=spec.source_class,
        sheet_id=spec.sheet_id,
        source_row=row_index + 1,
        target_identity_hash=envelope.target_identity_hash,
        source_fingerprint=envelope.source_fingerprint,
    )


def _review(source_ref, reason_codes, provenance) -> SourceReviewFact:
    return _new(
        SourceReviewFact,
        provenance,
        source_ref=source_ref,
        reason_codes=tuple(sorted(set(reason_codes))),
    )


def _id_diagnostics(provenance) -> Tuple[IdDiagnostic, ...]:
    definitions = (
        (IdNamespace.MREC, "merchant_case", "MREC", "MERCHANT_MREC_ASSIGNMENT_DEFERRED"),
        (IdNamespace.BRD, "merchant_case", "BRD", "MERCHANT_BRD_ASSIGNMENT_DEFERRED"),
        (IdNamespace.NONE, "merchant_case", "ID Review Status", "MERCHANT_ID_REVIEW_STATUS_DEFERRED"),
        (IdNamespace.MET, "public_metric", "MET", "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED"),
        (IdNamespace.BRD, "handle_mapping", "品牌 ID 對照", "BRAND_ID_MAPPING_AUTHORITY_DEFERRED"),
        (IdNamespace.BRD, "handle_mapping", "品牌 ID 初始化審核", "BRAND_ID_INITIAL_REVIEW_DEFERRED"),
    )
    return tuple(
        _new(
            IdDiagnostic,
            provenance,
            namespace=namespace,
            source_class=source_class,
            field_name_or_surface=surface,
            reason_code=reason,
        )
        for namespace, source_class, surface, reason in definitions
    )


def _sheet(snapshot: SpreadsheetSnapshot, sheet_id: int) -> SheetSnapshot:
    for sheet in snapshot.sheets:
        if sheet.sheet_id == sheet_id:
            return sheet
    _fail("WP3_SOURCE_SHEET_MISSING")


def _column_index(column: str) -> int:
    return ord(column) - ord("A")


def _column_letter(index: int) -> str:
    return chr(ord("A") + index)


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
