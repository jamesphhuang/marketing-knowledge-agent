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
    BRD_AUTHORITY_DEFERRED = "BRD_AUTHORITY_DEFERRED"
    HANDLE_MAPPING_EVIDENCE_ONLY = "HANDLE_MAPPING_EVIDENCE_ONLY"


class SafeSourceRef:
    """A redacted source locator whose digest is a review reference, not an ID."""

    __slots__ = ("_source_class", "_sheet_id", "_source_row", "_source_ref")

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
        return self._website_refs

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return self._reason_codes

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BRAND_REVIEW_CANDIDATE_IMMUTABLE")

    def __repr__(self) -> str:
        return (
            "BrandReviewCandidate("
            f"candidate_ref={self.candidate_ref!r}, "
            f"classification={self.classification.value!r}, "
            f"source_count={len(self.source_refs)}, content=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class _BrandEvidence:
    source_ref: SafeSourceRef
    normalized_name: Optional[str]
    normalized_handle: Optional[str]
    canonical_urls: Tuple[str, ...]
    unsafe_website_evidence: bool
    handle_mapping: bool
    multiple_urls_in_one_cell: bool

    def __repr__(self) -> str:
        return (
            "_BrandEvidence("
            f"source_ref={self.source_ref.source_ref!r}, content=<redacted>)"
        )


def _new_safe_source_ref(
    *,
    source_class: str,
    sheet_id: int,
    source_row: int,
    target_identity_hash: str,
    source_fingerprint: str,
) -> SafeSourceRef:
    if (
        type(source_class) is not str
        or not source_class
        or type(sheet_id) is not int
        or sheet_id < 0
        or type(source_row) is not int
        or source_row <= 0
        or not _is_hash(target_identity_hash)
        or not _is_hash(source_fingerprint)
    ):
        _fail("SAFE_SOURCE_REF_INPUT_INVALID")
    payload = _canonical_json(
        {
            "sheet_id": sheet_id,
            "source_class": source_class,
            "source_fingerprint": source_fingerprint,
            "source_row": source_row,
            "target_identity_hash": target_identity_hash,
        }
    )
    value = object.__new__(SafeSourceRef)
    object.__setattr__(value, "_source_class", source_class)
    object.__setattr__(value, "_sheet_id", sheet_id)
    object.__setattr__(value, "_source_row", source_row)
    object.__setattr__(
        value,
        "_source_ref",
        "sha256:" + hashlib.sha256(_SOURCE_REF_DOMAIN + payload).hexdigest(),
    )
    return value


def _new_brand_evidence(
    *,
    source_ref: SafeSourceRef,
    normalized_name: Optional[str],
    normalized_handle: Optional[str],
    canonical_urls: Iterable[str],
    unsafe_website_evidence: bool,
    handle_mapping: bool,
    multiple_urls_in_one_cell: bool,
) -> _BrandEvidence:
    if type(source_ref) is not SafeSourceRef:
        _fail("BRAND_EVIDENCE_SOURCE_REF_INVALID")
    urls = tuple(sorted(set(canonical_urls)))
    if any(type(url) is not str or not url for url in urls):
        _fail("BRAND_EVIDENCE_URL_INVALID")
    if normalized_name is not None and (
        type(normalized_name) is not str or not normalized_name
    ):
        _fail("BRAND_EVIDENCE_NAME_INVALID")
    if normalized_handle is not None and (
        type(normalized_handle) is not str or not normalized_handle
    ):
        _fail("BRAND_EVIDENCE_HANDLE_INVALID")
    if any(
        type(value) is not bool
        for value in (
            unsafe_website_evidence,
            handle_mapping,
            multiple_urls_in_one_cell,
        )
    ):
        _fail("BRAND_EVIDENCE_FLAG_INVALID")
    return _BrandEvidence(
        source_ref=source_ref,
        normalized_name=normalized_name,
        normalized_handle=normalized_handle,
        canonical_urls=urls,
        unsafe_website_evidence=unsafe_website_evidence,
        handle_mapping=handle_mapping,
        multiple_urls_in_one_cell=multiple_urls_in_one_cell,
    )


def _build_brand_review_candidates(
    evidence: Iterable[_BrandEvidence],
) -> Tuple[BrandReviewCandidate, ...]:
    """Build exact-evidence connected components in deterministic order."""

    records = tuple(evidence)
    if any(type(item) is not _BrandEvidence for item in records):
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

    candidates = [
        _candidate_from_component(
            component,
            name_collision=index in colliding_components,
        )
        for index, component in enumerate(component_records)
    ]
    return tuple(sorted(candidates, key=lambda item: item.candidate_ref))


def _candidate_from_component(
    component: Iterable[_BrandEvidence], *, name_collision: bool
) -> BrandReviewCandidate:
    records = tuple(component)
    handles = tuple(
        sorted({item.normalized_handle for item in records if item.normalized_handle})
    )
    urls = tuple(sorted({url for item in records for url in item.canonical_urls}))
    sources = tuple(sorted({item.source_ref for item in records}, key=lambda item: item._key()))
    unsafe = any(item.unsafe_website_evidence for item in records)
    mapping = any(item.handle_mapping for item in records)
    one_cell_conflict = any(item.multiple_urls_in_one_cell for item in records)

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
    ):
        classification = BrandCandidateClassification.UNIQUE_EVIDENCE
    else:
        classification = BrandCandidateClassification.AMBIGUOUS
        if len(handles) != 1:
            reasons.add(BrandCandidateReason.INSUFFICIENT_IDENTITY_EVIDENCE.value)

    website_refs = tuple(_website_ref(url) for url in urls)
    hosts = tuple(sorted({_safe_hostname(url) for url in urls}))
    primitive = {
        "classification": classification.value,
        "handles": handles,
        "source_refs": [item.source_ref for item in sources],
        "website_refs": website_refs,
    }
    candidate_ref = "sha256:" + hashlib.sha256(
        _CANDIDATE_REF_DOMAIN + _canonical_json(primitive)
    ).hexdigest()
    candidate = object.__new__(BrandReviewCandidate)
    object.__setattr__(candidate, "_candidate_ref", candidate_ref)
    object.__setattr__(candidate, "_classification", classification)
    object.__setattr__(candidate, "_source_refs", sources)
    object.__setattr__(
        candidate, "_normalized_handle", handles[0] if len(handles) == 1 else None
    )
    object.__setattr__(candidate, "_website_hosts", hosts)
    object.__setattr__(candidate, "_website_refs", website_refs)
    object.__setattr__(candidate, "_reason_codes", tuple(sorted(reasons)))
    return candidate


def _website_ref(canonical_url: str) -> str:
    return "sha256:" + hashlib.sha256(
        _WEBSITE_REF_DOMAIN + canonical_url.encode("utf-8")
    ).hexdigest()


def _safe_hostname(canonical_url: str) -> str:
    hostname = urlsplit(canonical_url).hostname
    if not hostname:
        _fail("CANONICAL_WEBSITE_HOST_MISSING")
    return hostname


def _evidence_order_key(item: _BrandEvidence) -> tuple:
    return (
        item.source_ref._key(),
        item.normalized_handle or "",
        item.canonical_urls,
        item.normalized_name or "",
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _is_hash(value: object) -> bool:
    if type(value) is not str or not value.startswith("sha256:"):
        return False
    digest = value[7:]
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


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
