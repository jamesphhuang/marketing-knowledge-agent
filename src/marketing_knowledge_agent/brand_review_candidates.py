"""Redacted, non-authoritative Sprint 1 WP3 brand-review candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit


BRAND_REVIEW_CANDIDATE_SCHEMA_VERSION = (
    "s1-wp3-brand-review-candidate-v1"
)
_SOURCE_REF_DOMAIN = b"marketing-knowledge-agent:wp3:safe-source-ref:v1\0"
_CANDIDATE_REF_DOMAIN = b"marketing-knowledge-agent:wp3:brand-candidate:v1\0"
_WEBSITE_REF_DOMAIN = b"marketing-knowledge-agent:wp3:website-ref:v1\0"


class BrandCandidateError(ValueError):
    """Stable failure that never reflects source payloads."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class BrandCandidateClassification(str, Enum):
    UNIQUE_EVIDENCE = "UNIQUE_EVIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"


class BrandCandidateReason(str, Enum):
    EXACT_HANDLE_EVIDENCE = "EXACT_HANDLE_EVIDENCE"
    SAFE_WEBSITE_EVIDENCE = "SAFE_WEBSITE_EVIDENCE"
    INSUFFICIENT_IDENTITY_EVIDENCE = "INSUFFICIENT_IDENTITY_EVIDENCE"
    UNSAFE_WEBSITE_EVIDENCE = "UNSAFE_WEBSITE_EVIDENCE"
    MULTIPLE_SAFE_WEBSITES = "MULTIPLE_SAFE_WEBSITES"
    HANDLE_TO_WEBSITE_CONFLICT = "HANDLE_TO_WEBSITE_CONFLICT"
    WEBSITE_TO_HANDLE_CONFLICT = "WEBSITE_TO_HANDLE_CONFLICT"
    NAME_COLLISION_ACROSS_CANDIDATES = "NAME_COLLISION_ACROSS_CANDIDATES"
    NAME_DIVERGENCE_WITHIN_CANDIDATE = "NAME_DIVERGENCE_WITHIN_CANDIDATE"
    BRD_AUTHORITY_DEFERRED = "BRD_AUTHORITY_DEFERRED"
    HANDLE_MAPPING_EVIDENCE_ONLY = "HANDLE_MAPPING_EVIDENCE_ONLY"


class SafeSourceRef:
    """Pseudonymous ref; no secrecy, redaction, authenticity, or permanent ID."""

    # Guard scope: docs/governance/PYTHON_GUARD_THREAT_MODEL.md (T1/T2, not T3).

    __slots__ = (
        "_source_class", "_sheet_id", "_source_row", "_source_ref", "__weakref__",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "SafeSourceRef":
        raise TypeError("SAFE_SOURCE_REF_CONSTRUCTION_FORBIDDEN")

    @property
    def source_class(self) -> str:
        return self._source_class

    @property
    def sheet_id(self) -> int:
        return self._sheet_id

    @property
    def source_row(self) -> int:
        return self._source_row

    @property
    def source_ref(self) -> str:
        return self._source_ref

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SAFE_SOURCE_REF_IMMUTABLE")

    def __reduce__(self) -> object:
        raise TypeError("SAFE_SOURCE_REF_SERIALIZATION_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("SAFE_SOURCE_REF_SERIALIZATION_FORBIDDEN")

    def __repr__(self) -> str:
        return (
            "SafeSourceRef("
            f"source_class={self.source_class!r}, sheet_id={self.sheet_id!r}, "
            f"source_row={self.source_row!r}, source_ref={self.source_ref!r})"
        )

    def __hash__(self) -> int:
        return hash(self._key())

    def __eq__(self, other: object) -> bool:
        return type(other) is SafeSourceRef and self._key() == other._key()

    def _key(self) -> tuple:
        return (
            self.source_class,
            self.sheet_id,
            self.source_row,
            self.source_ref,
        )


class BrandReviewCandidate:
    """Safe v1 projection for human review; never a Brand or BRD decision."""

    __slots__ = (
        "_candidate_ref",
        "_classification",
        "_source_refs",
        "_normalized_handle",
        "_website_hosts",
        "_website_refs",
        "_reason_codes",
        "__weakref__",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "BrandReviewCandidate":
        raise TypeError("BRAND_REVIEW_CANDIDATE_CONSTRUCTION_FORBIDDEN")

    @property
    def schema_version(self) -> str:
        return BRAND_REVIEW_CANDIDATE_SCHEMA_VERSION

    @property
    def candidate_kind(self) -> str:
        return "BRAND_IDENTITY_EVIDENCE"

    @property
    def authority(self) -> str:
        return "NON_AUTHORITATIVE"

    @property
    def review_action(self) -> str:
        return "HUMAN_REVIEW_REQUIRED"

    @property
    def candidate_ref(self) -> str:
        """Batch review ref; not identity, continuity key, or authorization."""

        return self._candidate_ref

    @property
    def classification(self) -> BrandCandidateClassification:
        return self._classification

    @property
    def source_refs(self) -> Tuple[SafeSourceRef, ...]:
        return self._source_refs

    @property
    def normalized_handle(self) -> Optional[str]:
        return self._normalized_handle

    @property
    def website_hosts(self) -> Tuple[str, ...]:
        return self._website_hosts

    @property
    def website_refs(self) -> Tuple[str, ...]:
        """Pseudonymous refs; no secrecy, redaction, authenticity, or identity."""

        return self._website_refs

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return self._reason_codes

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BRAND_REVIEW_CANDIDATE_IMMUTABLE")

    def __reduce__(self) -> object:
        raise TypeError("BRAND_REVIEW_CANDIDATE_SERIALIZATION_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("BRAND_REVIEW_CANDIDATE_SERIALIZATION_FORBIDDEN")

    def __repr__(self) -> str:
        return (
            "BrandReviewCandidate("
            f"candidate_ref={self.candidate_ref!r}, "
            f"classification={self.classification.value!r}, "
            f"source_count={len(self.source_refs)}, content=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class _UntrustedBrandEvidence:
    """Pure graph input; only the WP3 pipeline may bind it to trusted output."""

    source_ref: SafeSourceRef
    normalized_name: Optional[str]
    normalized_handle: Optional[str]
    canonical_urls: Tuple[str, ...]
    unsafe_website_evidence: bool
    handle_mapping: bool
    multiple_urls_in_one_cell: bool

    def __post_init__(self) -> None:
        urls = tuple(sorted(set(self.canonical_urls)))
        if (
            type(self.source_ref) is not SafeSourceRef
            or any(type(url) is not str or not url for url in urls)
            or (
                self.normalized_name is not None
                and (type(self.normalized_name) is not str or not self.normalized_name)
            )
            or (
                self.normalized_handle is not None
                and (
                    type(self.normalized_handle) is not str
                    or not self.normalized_handle
                )
            )
            or any(
                type(value) is not bool
                for value in (
                    self.unsafe_website_evidence,
                    self.handle_mapping,
                    self.multiple_urls_in_one_cell,
                )
            )
        ):
            _fail("BRAND_EVIDENCE_INPUT_INVALID")
        object.__setattr__(self, "canonical_urls", urls)

    def __repr__(self) -> str:
        return (
            "_UntrustedBrandEvidence("
            f"source_ref={self.source_ref.source_ref!r}, content=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class _UntrustedBrandCandidateProjection:
    """Pure candidate classification; it carries no construction authority."""

    classification: BrandCandidateClassification
    source_refs: Tuple[SafeSourceRef, ...]
    handles: Tuple[str, ...]
    canonical_urls: Tuple[str, ...]
    reason_codes: Tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "_UntrustedBrandCandidateProjection("
            f"classification={self.classification.value!r}, "
            f"source_count={len(self.source_refs)}, content=<redacted>)"
        )


def _safe_source_ref_digest(
    source_class: str,
    sheet_id: int,
    source_row: int,
    target_identity_hash: str,
    source_fingerprint: str,
) -> str:
    """Compute an untrusted digest; this function cannot mint a source ref."""

    payload = _canonical_json(
        {
            "sheet_id": sheet_id,
            "source_class": source_class,
            "source_fingerprint": source_fingerprint,
            "source_row": source_row,
            "target_identity_hash": target_identity_hash,
        }
    )
    return "sha256:" + hashlib.sha256(_SOURCE_REF_DOMAIN + payload).hexdigest()


def _project_brand_review_candidates(
    evidence: Iterable[_UntrustedBrandEvidence],
) -> Tuple[_UntrustedBrandCandidateProjection, ...]:
    """Classify exact-evidence components without minting trusted candidates."""

    records = tuple(evidence)
    if any(type(item) is not _UntrustedBrandEvidence for item in records):
        _fail("BRAND_EVIDENCE_TYPE_INVALID")
    if not records:
        return ()

    ordered = tuple(sorted(records, key=_evidence_order_key))
    parent = list(range(len(ordered)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    handle_owner: Dict[str, int] = {}
    url_owner: Dict[str, int] = {}
    for index, item in enumerate(ordered):
        if item.normalized_handle is not None:
            previous = handle_owner.setdefault(item.normalized_handle, index)
            union(index, previous)
        for url in item.canonical_urls:
            previous = url_owner.setdefault(url, index)
            union(index, previous)

    components: Dict[int, list] = {}
    for index, item in enumerate(ordered):
        components.setdefault(find(index), []).append(item)
    component_records = list(components.values())

    name_components: Dict[str, set] = {}
    for component_index, component in enumerate(component_records):
        for name in {item.normalized_name for item in component if item.normalized_name}:
            name_components.setdefault(name, set()).add(component_index)
    colliding_components = {
        index
        for indexes in name_components.values()
        if len(indexes) > 1
        for index in indexes
    }

    projections = [
        _projection_from_component(
            component,
            name_collision=index in colliding_components,
        )
        for index, component in enumerate(component_records)
    ]
    return tuple(sorted(projections, key=_projection_order_key))


def _projection_from_component(
    component: Iterable[_UntrustedBrandEvidence], *, name_collision: bool
) -> _UntrustedBrandCandidateProjection:
    records = tuple(component)
    handles = tuple(
        sorted({item.normalized_handle for item in records if item.normalized_handle})
    )
    urls = tuple(sorted({url for item in records for url in item.canonical_urls}))
    sources = tuple(sorted({item.source_ref for item in records}, key=lambda item: item._key()))
    unsafe = any(item.unsafe_website_evidence for item in records)
    mapping = any(item.handle_mapping for item in records)
    one_cell_conflict = any(item.multiple_urls_in_one_cell for item in records)
    names = {
        item.normalized_name for item in records if item.normalized_name is not None
    }
    name_divergence = len(names) > 1

    reasons = {BrandCandidateReason.BRD_AUTHORITY_DEFERRED.value}
    if handles:
        reasons.add(BrandCandidateReason.EXACT_HANDLE_EVIDENCE.value)
    if urls:
        reasons.add(BrandCandidateReason.SAFE_WEBSITE_EVIDENCE.value)
    if unsafe:
        reasons.add(BrandCandidateReason.UNSAFE_WEBSITE_EVIDENCE.value)
    if mapping:
        reasons.add(BrandCandidateReason.HANDLE_MAPPING_EVIDENCE_ONLY.value)
    if name_collision:
        reasons.add(BrandCandidateReason.NAME_COLLISION_ACROSS_CANDIDATES.value)
    if name_divergence:
        reasons.add(BrandCandidateReason.NAME_DIVERGENCE_WITHIN_CANDIDATE.value)

    conflict = one_cell_conflict or len(handles) > 1 or len(urls) > 1
    if len(urls) > 1:
        reasons.add(BrandCandidateReason.MULTIPLE_SAFE_WEBSITES.value)
    if len(handles) == 1 and len(urls) > 1:
        reasons.add(BrandCandidateReason.HANDLE_TO_WEBSITE_CONFLICT.value)
    if len(handles) > 1 and len(urls) == 1:
        reasons.add(BrandCandidateReason.WEBSITE_TO_HANDLE_CONFLICT.value)
    if len(handles) > 1 and len(urls) > 1:
        reasons.update(
            {
                BrandCandidateReason.HANDLE_TO_WEBSITE_CONFLICT.value,
                BrandCandidateReason.WEBSITE_TO_HANDLE_CONFLICT.value,
            }
        )

    if conflict:
        classification = BrandCandidateClassification.CONFLICTING
    elif (
        len(handles) == 1
        and len(urls) <= 1
        and not unsafe
        and not name_collision
        and not name_divergence
    ):
        classification = BrandCandidateClassification.UNIQUE_EVIDENCE
    else:
        classification = BrandCandidateClassification.AMBIGUOUS
        if len(handles) != 1:
            reasons.add(BrandCandidateReason.INSUFFICIENT_IDENTITY_EVIDENCE.value)

    return _UntrustedBrandCandidateProjection(
        classification=classification,
        source_refs=sources,
        handles=handles,
        canonical_urls=urls,
        reason_codes=tuple(sorted(reasons)),
    )


def _candidate_ref_digest(
    classification: BrandCandidateClassification,
    handles: Tuple[str, ...],
    source_refs: Tuple[SafeSourceRef, ...],
    website_refs: Tuple[str, ...],
) -> str:
    """Compute an untrusted review digest; this cannot mint a candidate."""

    primitive = {
        "classification": classification.value,
        "handles": handles,
        "source_refs": [item.source_ref for item in source_refs],
        "website_refs": website_refs,
    }
    return "sha256:" + hashlib.sha256(
        _CANDIDATE_REF_DOMAIN + _canonical_json(primitive)
    ).hexdigest()


def _website_ref(canonical_url: str) -> str:
    """Compute a deterministic pseudonymous ref, not a redaction guarantee."""

    return "sha256:" + hashlib.sha256(
        _WEBSITE_REF_DOMAIN + canonical_url.encode("utf-8")
    ).hexdigest()


def _safe_hostname(canonical_url: str) -> str:
    hostname = urlsplit(canonical_url).hostname
    if not hostname:
        _fail("CANONICAL_WEBSITE_HOST_MISSING")
    return hostname


def _evidence_order_key(item: _UntrustedBrandEvidence) -> tuple:
    return (
        item.source_ref._key(),
        item.normalized_handle or "",
        item.canonical_urls,
        item.normalized_name or "",
    )


def _projection_order_key(item: _UntrustedBrandCandidateProjection) -> tuple:
    website_refs = tuple(_website_ref(url) for url in item.canonical_urls)
    return (
        _candidate_ref_digest(
            item.classification,
            item.handles,
            item.source_refs,
            website_refs,
        ),
        tuple(source._key() for source in item.source_refs),
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fail(code: str) -> None:
    raise BrandCandidateError(code) from None


__all__ = [
    "BRAND_REVIEW_CANDIDATE_SCHEMA_VERSION",
    "BrandCandidateClassification",
    "BrandCandidateError",
    "BrandCandidateReason",
    "BrandReviewCandidate",
    "SafeSourceRef",
]
