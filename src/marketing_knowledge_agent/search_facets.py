"""Read-only facet catalog for the Slack structured (faceted) search MVP.

A facet catalog answers one narrow question for the Slack modal: *which interview years, Sales
Category LV2 values and content tags are worth offering as a button, right now?* It is built once
from two things this process already trusts read-only -- a pinned Search Taxonomy Authority and the
formal content index -- and it never widens either: a value only appears here when it is both a
canonical Authority value *and* actually carried by at least one document this channel could ever be
shown.

This module never writes to the taxonomy workbook, the Obsidian Vault or the content index, and it
never mutates them. It also never discloses an eligible-record count anywhere a Slack user could
read it; the counts exist only so this module itself can decide which options to keep, and are
otherwise a diagnostic detail for tests and operators.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .indexing import SQLiteIndex
from .governance import filter_restricted_results
from .models import SearchFilters, SearchResult
from .query_gating import apply_intent_gating
from .query_planning import normalize_query_text
from .retrieval import matches_filters
from .search_taxonomy import FIELD_CONTENT_TAGS, FIELD_SALES_CATEGORY_LV2, SearchTaxonomy
from .structured_search import (
    STRUCTURED_REQUEST_SCHEMA_VERSION,
    StructuredSearchGovernanceError,
    assert_readable_content_index,
    load_required_governance_index,
)


# Bumped only when this builder's own eligibility rules change shape -- a new governance filter, a
# different dedupe unit. Folded into ``catalog_version`` so a code change invalidates every catalog
# and submission built under the old rules, the same way an Authority or index change does.
CATALOG_BUILDER_SCHEMA_VERSION = "1"


class FacetCatalogError(ValueError):
    """Raised when the facet catalog cannot be built from a trustworthy read of its inputs."""


@dataclass(frozen=True)
class FacetYearOption:
    year: int
    eligible_count: int


@dataclass(frozen=True)
class FacetValueOption:
    canonical_value: str
    eligible_count: int


@dataclass(frozen=True)
class FacetCatalog:
    """Immutable snapshot of what the Slack facet modal may offer.

    ``catalog_version`` is a pure function of the Authority pin, the content index bytes, this
    builder's own schema version, and the Slack submission wire schema. A Slack submission carries
    the version it was opened under, and a live catalog that no longer matches refuses the
    submission rather than execute against options that may no longer be eligible.
    """

    catalog_version: str
    generated_at: str
    taxonomy_workbook_sha256: str
    content_index_generation_id: str
    interview_years: Tuple[FacetYearOption, ...]
    sales_category_lv2: Tuple[FacetValueOption, ...]
    content_tags: Tuple[FacetValueOption, ...]

    def is_valid_year(self, year: object) -> bool:
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            return False
        return any(option.year == year_int for option in self.interview_years)

    def is_valid_sales_category_lv2(self, value: object) -> bool:
        normalized = normalize_query_text(value)
        return bool(normalized) and any(
            normalize_query_text(option.canonical_value) == normalized
            for option in self.sales_category_lv2
        )

    def is_valid_content_tag(self, value: object) -> bool:
        normalized = normalize_query_text(value)
        return bool(normalized) and any(
            normalize_query_text(option.canonical_value) == normalized for option in self.content_tags
        )


def build_facet_catalog(
    db_path: Path,
    taxonomy: SearchTaxonomy,
    restricted_customers_path: Path,
) -> FacetCatalog:
    """Build one read-only facet catalog from the pinned Authority and the live content index.

    Eligibility mirrors exactly what an external Slack query would be allowed to retrieve: the same
    ``SearchFilters(intent="external")`` gating, the same non-retrievable record-type exclusion, and
    the same restricted-customer denylist. A document is counted once per distinct ``document_id``,
    never once per chunk, so a long document never outweighs a short one in a facet count.

    Both inputs are required and both fail closed. A denylist that could not be loaded would produce
    a catalog whose options were computed without it -- offering a facet value whose only carrier is
    a restricted customer, and thereby disclosing that the customer exists through the option list
    alone, before any search is ever run.
    """
    try:
        # Checked before opening: ``sqlite3.connect`` would otherwise create an empty database at
        # this path, and a read-only surface must not bring a content index into existence.
        assert_readable_content_index(db_path)
        # Raises rather than warning: an ignored warning here becomes a governance-free option list.
        governance_index = load_required_governance_index(restricted_customers_path)
    except StructuredSearchGovernanceError as exc:
        # Re-typed so this function has exactly one failure type for a caller to handle, without
        # losing the original cause.
        raise FacetCatalogError(str(exc)) from exc

    try:
        index = SQLiteIndex(Path(db_path))
        indexed_chunks = index.load_chunks()
    except (sqlite3.Error, OSError) as exc:
        raise FacetCatalogError(f"無法讀取內容索引 {db_path} 以建立 facet catalog：{exc}") from exc

    filters = apply_intent_gating(SearchFilters(intent="external"))
    authority_lv2 = {
        normalize_query_text(value) for value in taxonomy.canonical_values(FIELD_SALES_CATEGORY_LV2)
    }
    authority_tags = {
        normalize_query_text(value) for value in taxonomy.canonical_values(FIELD_CONTENT_TAGS)
    }

    seen_documents: set = set()
    year_counts: Dict[int, int] = {}
    lv2_counts: Dict[str, int] = {}
    lv2_display: Dict[str, str] = {}
    tag_counts: Dict[str, int] = {}
    tag_display: Dict[str, str] = {}

    for indexed_chunk in indexed_chunks:
        chunk = indexed_chunk.chunk
        if chunk.document_id in seen_documents:
            continue
        metadata = chunk.metadata
        if not matches_filters(metadata, filters):
            continue
        kept, _removed = filter_restricted_results([SearchResult(chunk=chunk, score=0.0)], governance_index)
        if not kept:
            continue
        seen_documents.add(chunk.document_id)

        if metadata.interview_year is not None:
            year_counts[metadata.interview_year] = year_counts.get(metadata.interview_year, 0) + 1

        if metadata.sales_category_lv2:
            normalized_lv2 = normalize_query_text(metadata.sales_category_lv2)
            if normalized_lv2 in authority_lv2:
                lv2_display.setdefault(normalized_lv2, metadata.sales_category_lv2)
                lv2_counts[normalized_lv2] = lv2_counts.get(normalized_lv2, 0) + 1

        for tag in metadata.content_tags:
            normalized_tag = normalize_query_text(tag)
            if normalized_tag in authority_tags:
                tag_display.setdefault(normalized_tag, tag)
                tag_counts[normalized_tag] = tag_counts.get(normalized_tag, 0) + 1

    interview_years = tuple(
        FacetYearOption(year=year, eligible_count=year_counts[year])
        for year in sorted(year_counts, reverse=True)
    )
    sales_category_lv2 = tuple(
        FacetValueOption(canonical_value=lv2_display[key], eligible_count=lv2_counts[key])
        for key in sorted(lv2_counts, key=lambda item: lv2_display[item])
    )
    content_tags = tuple(
        FacetValueOption(canonical_value=tag_display[key], eligible_count=tag_counts[key])
        for key in sorted(tag_counts, key=lambda item: tag_display[item])
    )

    content_index_generation_id = _hash_file(Path(db_path))
    catalog_version = _catalog_version(taxonomy.workbook_sha256, content_index_generation_id)

    return FacetCatalog(
        catalog_version=catalog_version,
        generated_at=_utc_now(),
        taxonomy_workbook_sha256=taxonomy.workbook_sha256,
        content_index_generation_id=content_index_generation_id,
        interview_years=interview_years,
        sales_category_lv2=sales_category_lv2,
        content_tags=content_tags,
    )


def _catalog_version(taxonomy_workbook_sha256: str, content_index_generation_id: str) -> str:
    """Everything a submission must have been built under, folded into one opaque version.

    ``STRUCTURED_REQUEST_SCHEMA_VERSION`` is included alongside this builder's own version because
    the two change independently and both invalidate an in-flight modal. A view opened before a
    wire-schema change and submitted after one would otherwise be decoded under rules it was never
    rendered for -- which is how a year-restricted search quietly becomes an all-years one.
    """
    digest = hashlib.sha256(
        "|".join(
            [
                CATALOG_BUILDER_SCHEMA_VERSION,
                STRUCTURED_REQUEST_SCHEMA_VERSION,
                taxonomy_workbook_sha256,
                content_index_generation_id,
            ]
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
