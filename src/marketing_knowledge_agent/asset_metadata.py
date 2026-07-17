from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PUBLICATION_STATUS_VALUES = (
    "published",
    "scheduled",
    "draft",
    "unpublished",
    "archived",
    "unknown",
)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
NONCANONICAL_HOST_PATHS = {
    ("google.com", "/search"),
    ("www.google.com", "/search"),
    ("google.com", "/url"),
    ("www.google.com", "/url"),
    ("l.facebook.com", "/l.php"),
}
SHORTENER_HOSTS = {
    "bit.ly",
    "goo.gl",
    "reurl.cc",
    "tinyurl.com",
}


@dataclass(frozen=True)
class AssetMetadataFieldDefinition:
    canonical_name: str
    data_type: str
    valid_values: Tuple[str, ...]
    empty_rule: str
    value_scope: str
    authoritative_source: Optional[str]
    secondary_sources: Tuple[str, ...]
    auto_derivation_allowed: bool
    derivation_rule: str
    conflict_policy: str
    provenance_fields: Tuple[str, ...]
    confidence_levels: Tuple[str, ...]
    retrieval_index_eligible: bool
    allowed_operators: Tuple[str, ...]


def _asset_field(
    canonical_name: str,
    data_type: str,
    *,
    valid_values: Sequence[str] = (),
    empty_rule: str,
    value_scope: str,
    authoritative_source: Optional[str],
    secondary_sources: Sequence[str] = (),
    auto_derivation_allowed: bool = False,
    derivation_rule: str = "none",
    conflict_policy: str = "human_review",
    retrieval_index_eligible: bool = False,
    allowed_operators: Sequence[str] = ("exact",),
) -> AssetMetadataFieldDefinition:
    return AssetMetadataFieldDefinition(
        canonical_name=canonical_name,
        data_type=data_type,
        valid_values=tuple(valid_values),
        empty_rule=empty_rule,
        value_scope=value_scope,
        authoritative_source=authoritative_source,
        secondary_sources=tuple(secondary_sources),
        auto_derivation_allowed=auto_derivation_allowed,
        derivation_rule=derivation_rule,
        conflict_policy=conflict_policy,
        provenance_fields=("source", "source_location", "provenance", "confidence"),
        confidence_levels=("high", "medium", "low", "none"),
        retrieval_index_eligible=retrieval_index_eligible,
        allowed_operators=tuple(allowed_operators),
    )


ASSET_METADATA_FIELD_REGISTRY: Dict[str, AssetMetadataFieldDefinition] = {
    "asset_url": _asset_field(
        "asset_url",
        "url",
        empty_rule="blank means no asset-level URL evidence",
        value_scope="asset_level",
        authoritative_source="reviewed asset source URL",
        secondary_sources=("Excel cell hyperlink", "reviewed Vault frontmatter"),
        derivation_rule="copy an exact direct URL candidate only; never infer publication",
    ),
    "canonical_url": _asset_field(
        "canonical_url",
        "url",
        empty_rule="blank means canonical target is not verified",
        value_scope="asset_level",
        authoritative_source="publisher canonical metadata or human-confirmed canonical URL",
        secondary_sources=("clean direct asset URL",),
        derivation_rule="tracking parameters may be removed only as a review candidate",
    ),
    "published_at": _asset_field(
        "published_at",
        "date",
        empty_rule="blank when no exact asset-level publication date exists",
        value_scope="asset_level",
        authoritative_source="publisher asset metadata or human-reviewed source",
        secondary_sources=("explicit asset-level Vault field",),
        derivation_rule="never copy interview, capture, record publish, created, or updated dates",
        allowed_operators=("eq", "range", "before", "after"),
    ),
    "publication_status": _asset_field(
        "publication_status",
        "enum",
        valid_values=PUBLICATION_STATUS_VALUES,
        empty_rule="unknown when asset-level evidence is absent",
        value_scope="asset_level",
        authoritative_source="publisher state or human-reviewed asset evidence",
        secondary_sources=("explicit asset-level Vault field",),
        derivation_rule="never derive from URL presence or parent record status",
        allowed_operators=("exact", "in"),
    ),
    "interview_date": _asset_field(
        "interview_date",
        "date",
        empty_rule="blank; interview_year is not a date",
        value_scope="record_level",
        authoritative_source="interview operations record",
        secondary_sources=("explicit reviewed Excel field",),
        derivation_rule="never derive from interview_year or publication date",
        allowed_operators=("eq", "range", "before", "after"),
    ),
    "interview_status": _asset_field(
        "interview_status",
        "enum",
        valid_values=("planned", "scheduled", "completed", "cancelled", "unknown"),
        empty_rule="unknown; merchant relationship status is not interview status",
        value_scope="record_level",
        authoritative_source="interview workflow system",
        secondary_sources=(),
        derivation_rule="none",
        allowed_operators=("exact", "in"),
    ),
    "review_status": _asset_field(
        "review_status",
        "enum",
        valid_values=("pending", "approved", "rejected", "needs_update", "unknown"),
        empty_rule="unknown; governance review_decision is not an asset review status",
        value_scope="asset_level",
        authoritative_source="asset metadata review workflow",
        secondary_sources=(),
        derivation_rule="do not map record review_decision without explicit semantics",
        allowed_operators=("exact", "in"),
    ),
    "partner_name": _asset_field(
        "partner_name",
        "string",
        empty_rule="blank when merchant/partner entity type is not explicitly separated",
        value_scope="record_level",
        authoritative_source="reviewed merchant/partner identity source",
        secondary_sources=("Excel shared merchant / partner name column",),
        derivation_rule="never classify a shared name as partner without entity_type evidence",
    ),
}


def direct_asset_url_candidate(urls: Sequence[str]) -> dict:
    valid = [_normalize_source_url(url) for url in urls]
    valid = _unique_urls(url for url in valid if url)
    if not valid:
        return _candidate("", "missing_evidence", "no direct asset URL evidence", "none", False, "needs_source")
    direct = [url for url in valid if not _is_noncanonical_url(url)]
    if not direct:
        return _candidate("", "noncanonical_source_url", "only search, redirect, or short URL evidence exists", "low", True, "manual_review")
    if len(direct) > 1:
        return _candidate("", "conflicting_candidates", "multiple distinct direct asset URLs exist", "low", True, "manual_review")
    return _candidate(direct[0], "none", "exact direct URL from an asset cell hyperlink", "high", True, "approve_candidate")


def canonical_url_candidate(urls: Sequence[str]) -> dict:
    normalized_candidates = []
    tracking_removed = False
    noncanonical_seen = False
    for raw_url in _unique_urls(urls):
        normalized = _normalize_source_url(raw_url)
        if not normalized:
            continue
        if _is_noncanonical_url(normalized):
            noncanonical_seen = True
            continue
        canonical, removed = _remove_tracking_parameters(normalized)
        tracking_removed = tracking_removed or removed
        normalized_candidates.append(canonical)
    candidates = _unique_urls(normalized_candidates)
    if not candidates:
        status = "noncanonical_source_url" if noncanonical_seen else "missing_evidence"
        reason = "search, redirect, tracking-only, or short URLs are not canonical evidence" if noncanonical_seen else "no canonical URL evidence"
        return _candidate("", status, reason, "none", noncanonical_seen, "manual_review" if noncanonical_seen else "needs_source")
    if len(candidates) > 1:
        return _candidate("", "conflicting_candidates", "multiple canonical URL candidates disagree", "low", True, "manual_review")
    if tracking_removed:
        return _candidate(candidates[0], "tracking_parameters_removed", "tracking parameters removed for review; publisher canonical metadata is still required", "medium", True, "manual_review")
    return _candidate(candidates[0], "none", "single clean direct URL candidate; canonical status still requires review", "medium", True, "approve_candidate")


def parse_asset_date(value: object) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def is_enrichment_index_eligible(row: Mapping[str, object]) -> bool:
    return bool(
        row.get("review_decision") == "approve"
        and _text(row.get("proposed_value")) not in {"", "unknown"}
        and row.get("conflict_status") == "none"
    )


def _candidate(value: str, status: str, reason: str, confidence: str, review: bool, decision: str) -> dict:
    return {
        "proposed_value": value,
        "conflict_status": status,
        "reason": reason,
        "confidence": confidence,
        "review_required": review,
        "proposed_decision": decision,
    }


def _remove_tracking_parameters(url: str) -> Tuple[str, bool]:
    parsed = urlsplit(url)
    kept = []
    removed = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in TRACKING_QUERY_KEYS:
            removed = True
            continue
        kept.append((key, value))
    fragment_removed = bool(parsed.fragment)
    return (
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(kept), "")),
        removed or fragment_removed,
    )


def _normalize_source_url(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path or "/", parsed.query, parsed.fragment))


def _is_noncanonical_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    return host in SHORTENER_HOSTS or (host, path) in NONCANONICAL_HOST_PATHS


def _unique_urls(values: Iterable[object]):
    result = []
    seen = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
