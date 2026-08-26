"""Read-only Search Taxonomy Authority for the three controlled-vocabulary search fields.

This consumer accepts only an explicitly selected, externally pinned taxonomy workbook. It has no
production default, performs no discovery of a "latest" workbook, trusts neither the filename nor
any workbook-internal metadata, and exposes no mutation API.

The Authority answers exactly one question: *what does this term formally mean?* It never answers
*does the formal index actually contain that value?* -- that remains the runtime ``QueryCatalog``'s
question, and a value this Authority knows but the index does not must fail closed rather than
degrade into a broad search.

Scope is Sales Category LV1, Sales Category LV2 and content tags only. The content-tag sheet also
carries a third, blank-header reference-URL column; it is outside this contract and is excluded by
construction rather than by inference, because trailing blank header cells are trimmed before the
header shape is asserted.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .excel_preview import read_xlsx_workbook
from .query_planning import TAXONOMY_FIELDS, normalize_query_text


class SearchTaxonomyError(ValueError):
    """Raised when a taxonomy workbook cannot be trusted as an Authority."""


SHEET_SALES_CATEGORY = "Sales Category LV1 LV2"
SHEET_CONTENT_TAGS = "內容相關標籤"

FIELD_SALES_CATEGORY_LV1 = "sales_category_lv1"
FIELD_SALES_CATEGORY_LV2 = "sales_category_lv2"
FIELD_CONTENT_TAGS = "content_tags"

MATCH_TYPE_CANONICAL = "canonical"
MATCH_TYPE_EXPANSION = "expansion"

# The column layout is asserted against the workbook, never inferred from it. Each entry is
# (taxonomy field, canonical-value header, expansion-term header) at fixed column offsets, and the
# header row is row 1 in both sheets.
SALES_CATEGORY_LAYOUT: Tuple[Tuple[str, int, int], ...] = (
    (FIELD_SALES_CATEGORY_LV1, 0, 1),
    (FIELD_SALES_CATEGORY_LV2, 2, 3),
)
CONTENT_TAG_LAYOUT: Tuple[Tuple[str, int, int], ...] = ((FIELD_CONTENT_TAGS, 0, 1),)
EXPECTED_SALES_CATEGORY_HEADERS: Tuple[str, ...] = (
    "Sales Category LV1",
    "Sales Category LV1 擴充詞",
    "Sales Category LV2",
    "Sales Category LV2 擴充詞",
)
EXPECTED_CONTENT_TAG_HEADERS: Tuple[str, ...] = (
    "內容相關標籤",
    "內容相關標籤 擴充詞",
)
HEADER_ROW = 1

# Expansion cells are comma-separated. Only the half- and full-width comma separate terms: the
# ideographic comma is a term character here, not a separator -- the canonical content tag
# "直播串接（LINE、FB 等）" contains one, and splitting on it would shred a canonical name.
EXPANSION_SEPARATOR_RE = re.compile(r"[,，]")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MIN_SUGGESTION_LENGTH = 2
_DEFAULT_SUGGESTION_LIMIT = 3


@dataclass(frozen=True)
class TaxonomyEntry:
    """One (alias -> canonical value) binding stated by the Authority."""

    field: str
    canonical_value: str
    normalized_canonical: str
    alias_value: str
    normalized_alias: str
    match_type: str
    source_sheet: str
    source_row: int


class TaxonomyResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class TaxonomyResolution:
    status: TaxonomyResolutionStatus
    field: Optional[str] = None
    canonical_value: Optional[str] = None
    normalized_canonical: Optional[str] = None
    matched_alias: Optional[str] = None
    normalized_alias: Optional[str] = None
    match_type: Optional[str] = None
    candidates: Tuple[Tuple[str, str], ...] = ()
    detail: Optional[str] = None


@dataclass(frozen=True)
class TaxonomySuggestion:
    """A deterministic near-miss. Never a constraint, never an auto-correction."""

    normalized_alias: str
    field: str
    canonical_value: str


@dataclass(frozen=True)
class SearchTaxonomy:
    """Immutable read-side resolver for one externally pinned taxonomy workbook."""

    workbook_sha256: str
    workbook_path: str
    _entries_by_alias: Mapping[str, Tuple[TaxonomyEntry, ...]]
    _canonical_by_field: Mapping[str, Tuple[str, ...]]
    _aliases_longest_first: Tuple[str, ...]
    _expansion_term_counts: Mapping[str, int]
    _blank_expansion_term_count: int

    def canonical_values(self, field: str) -> Tuple[str, ...]:
        """Every canonical display value the Authority states for one field, verbatim."""
        return self._canonical_by_field.get(self._checked_field(field), ())

    def aliases_longest_first(self) -> Tuple[str, ...]:
        """Normalized aliases ordered longest first, then lexicographically.

        Length ordering is the only precedence rule offered to a caller scanning free text, and it
        is a property of the terms themselves rather than of workbook row order.
        """
        return self._aliases_longest_first

    def resolve(self, term: object, *, field: Optional[str] = None) -> TaxonomyResolution:
        """Resolve one term to one canonical value, or refuse.

        ``field`` restricts resolution to a single taxonomy domain, which is how an explicitly
        typed ``sales_category_lv1=...`` constraint escapes cross-level ambiguity.
        """
        if field is not None:
            field = self._checked_field(field)
        normalized = normalize_query_text(term)
        if not normalized:
            return TaxonomyResolution(
                status=TaxonomyResolutionStatus.NOT_FOUND,
                detail="term is empty after normalization",
            )

        entries = self._entries_by_alias.get(normalized, ())
        if field is not None:
            entries = tuple(entry for entry in entries if entry.field == field)
        if not entries:
            return TaxonomyResolution(
                status=TaxonomyResolutionStatus.NOT_FOUND,
                normalized_alias=normalized,
                detail="term is not in the pinned Search Taxonomy Authority",
            )

        preferred = _preferred_entries(entries)
        distinct = tuple(
            sorted({(entry.field, entry.normalized_canonical) for entry in preferred})
        )
        if len(distinct) > 1:
            return TaxonomyResolution(
                status=TaxonomyResolutionStatus.AMBIGUOUS,
                matched_alias=preferred[0].alias_value,
                normalized_alias=normalized,
                candidates=distinct,
                detail=(
                    "term resolves to more than one canonical value; "
                    "state the taxonomy field explicitly"
                ),
            )

        entry = preferred[0]
        return TaxonomyResolution(
            status=TaxonomyResolutionStatus.RESOLVED,
            field=entry.field,
            canonical_value=entry.canonical_value,
            normalized_canonical=entry.normalized_canonical,
            matched_alias=entry.alias_value,
            normalized_alias=normalized,
            match_type=entry.match_type,
        )

    def suggest_similar(
        self, term: object, *, limit: int = _DEFAULT_SUGGESTION_LIMIT
    ) -> Tuple[TaxonomySuggestion, ...]:
        """Deterministic edit-distance-one near misses, for display only.

        A suggestion never becomes a constraint and never picks a side of an ambiguity: a term one
        edit away from two different canonical values yields two suggestions, not a decision.
        """
        normalized = normalize_query_text(term)
        if len(normalized) < _MIN_SUGGESTION_LENGTH or normalized in self._entries_by_alias:
            return ()
        # Keyed by the canonical value rather than by the alias: two near-miss aliases naming the
        # same canonical value are one suggestion to a reader, not two.
        found: Dict[Tuple[str, str], TaxonomySuggestion] = {}
        for alias in sorted(self._entries_by_alias):
            if not _within_edit_distance_one(normalized, alias):
                continue
            for entry in _preferred_entries(self._entries_by_alias[alias]):
                found.setdefault(
                    (entry.field, entry.canonical_value),
                    TaxonomySuggestion(
                        normalized_alias=alias,
                        field=entry.field,
                        canonical_value=entry.canonical_value,
                    ),
                )
        ordered = sorted(
            found.values(),
            key=lambda item: (item.normalized_alias, item.field, item.canonical_value),
        )
        return tuple(ordered[: max(0, int(limit))])

    def diagnostic(self) -> Dict[str, object]:
        """Read-only accounting of what this pinned Authority contains."""
        intra_field: List[str] = []
        cross_field: List[str] = []
        for alias, entries in self._entries_by_alias.items():
            preferred = _preferred_entries(entries)
            distinct = {(entry.field, entry.normalized_canonical) for entry in preferred}
            if len({item[0] for item in distinct}) > 1:
                cross_field.append(alias)
            elif len(distinct) > 1:
                intra_field.append(alias)
        ambiguous = set(intra_field) | set(cross_field)
        return {
            "workbook_path": self.workbook_path,
            "workbook_sha256": self.workbook_sha256,
            "sheets": [SHEET_SALES_CATEGORY, SHEET_CONTENT_TAGS],
            "headers": {
                SHEET_SALES_CATEGORY: list(EXPECTED_SALES_CATEGORY_HEADERS),
                SHEET_CONTENT_TAGS: list(EXPECTED_CONTENT_TAG_HEADERS),
            },
            "fields": {
                field: {
                    "canonical_count": len(self._canonical_by_field.get(field, ())),
                    "expansion_term_count": self._expansion_term_counts.get(field, 0),
                    "distinct_alias_count": sum(
                        1
                        for entries in self._entries_by_alias.values()
                        if any(entry.field == field for entry in entries)
                    ),
                }
                for field in TAXONOMY_FIELDS
            },
            "distinct_alias_count": len(self._entries_by_alias),
            "unambiguous_alias_count": len(self._entries_by_alias) - len(ambiguous),
            "ambiguous_alias_count": len(ambiguous),
            "intra_field_collision_count": len(intra_field),
            "cross_field_ambiguity_count": len(cross_field),
            "blank_expansion_term_count": self._blank_expansion_term_count,
            "taxonomy_activated_as_production_default": False,
        }

    @staticmethod
    def _checked_field(field: str) -> str:
        if field not in TAXONOMY_FIELDS:
            raise SearchTaxonomyError(
                f"{field!r} is not a Search Taxonomy field; supported fields are "
                f"{list(TAXONOMY_FIELDS)}"
            )
        return field


def load_search_taxonomy(
    *,
    workbook_path: Path,
    expected_sha256: str,
) -> SearchTaxonomy:
    """Load one explicitly pinned taxonomy workbook, or refuse.

    Every refusal here is a refusal to answer a query with a vocabulary nobody adjudicated. A
    workbook that is missing, is a symlink to somewhere else, is not a readable xlsx, hashes
    differently than the pin, lacks either taxonomy sheet, or carries a different header shape stops
    the load before a single alias is indexed.
    """
    workbook_path = Path(workbook_path)

    # Checked before ``exists()``: a symlink to a missing target should report the symlink.
    if workbook_path.is_symlink():
        raise SearchTaxonomyError(
            f"taxonomy workbook {workbook_path} is a symlink; the Authority must be a regular file "
            "so the hash that is checked is the hash that is read"
        )
    if not workbook_path.exists():
        raise SearchTaxonomyError(f"taxonomy workbook {workbook_path} does not exist")
    if not workbook_path.is_file():
        raise SearchTaxonomyError(f"taxonomy workbook {workbook_path} is not a regular file")

    expected = (expected_sha256 or "").strip().lower()
    if not _SHA256_RE.match(expected):
        raise SearchTaxonomyError(
            f"expected sha256 for {workbook_path} must be 64 lowercase hex characters; "
            f"got {expected_sha256!r}"
        )

    actual = _hash_file(workbook_path)
    if actual != expected:
        raise SearchTaxonomyError(
            f"taxonomy workbook lineage mismatch for {workbook_path}: expected sha256 {expected}, "
            f"actual sha256 {actual}; this pin was adjudicated against a different workbook"
        )

    try:
        sheets = read_xlsx_workbook(workbook_path)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise SearchTaxonomyError(
            f"taxonomy workbook {workbook_path} could not be read as an xlsx workbook: {exc}"
        ) from exc

    entries: List[TaxonomyEntry] = []
    canonical_by_field: Dict[str, List[str]] = {field: [] for field in TAXONOMY_FIELDS}
    expansion_term_counts: Dict[str, int] = {field: 0 for field in TAXONOMY_FIELDS}
    blank_expansion_terms = 0

    for sheet_name, expected_headers, layout in (
        (SHEET_SALES_CATEGORY, EXPECTED_SALES_CATEGORY_HEADERS, SALES_CATEGORY_LAYOUT),
        (SHEET_CONTENT_TAGS, EXPECTED_CONTENT_TAG_HEADERS, CONTENT_TAG_LAYOUT),
    ):
        rows = _sheet_rows(sheets, sheet_name, workbook_path)
        _assert_headers(rows, sheet_name, expected_headers, workbook_path)
        for field, canonical_column, expansion_column in layout:
            sheet_entries, blanks = _read_column_pair(
                rows, sheet_name, field, canonical_column, expansion_column
            )
            blank_expansion_terms += blanks
            entries.extend(sheet_entries)
            canonical_by_field[field] = [
                entry.canonical_value
                for entry in sheet_entries
                if entry.match_type == MATCH_TYPE_CANONICAL
            ]
            expansion_term_counts[field] = sum(
                1 for entry in sheet_entries if entry.match_type == MATCH_TYPE_EXPANSION
            )

    for field in TAXONOMY_FIELDS:
        if not canonical_by_field[field]:
            raise SearchTaxonomyError(
                f"taxonomy workbook {workbook_path} states no canonical value for {field}"
            )

    by_alias: Dict[str, List[TaxonomyEntry]] = {}
    for entry in entries:
        bucket = by_alias.setdefault(entry.normalized_alias, [])
        # A term repeated inside one expansion cell states nothing new.
        if any(
            existing.field == entry.field
            and existing.normalized_canonical == entry.normalized_canonical
            and existing.match_type == entry.match_type
            for existing in bucket
        ):
            continue
        bucket.append(entry)

    aliases_longest_first = tuple(
        sorted(by_alias, key=lambda alias: (-len(alias), alias))
    )

    return SearchTaxonomy(
        workbook_sha256=actual,
        workbook_path=str(workbook_path),
        _entries_by_alias=MappingProxyType(
            {alias: tuple(bucket) for alias, bucket in by_alias.items()}
        ),
        _canonical_by_field=MappingProxyType(
            {field: tuple(values) for field, values in canonical_by_field.items()}
        ),
        _aliases_longest_first=aliases_longest_first,
        _expansion_term_counts=MappingProxyType(dict(expansion_term_counts)),
        _blank_expansion_term_count=blank_expansion_terms,
    )


def _read_column_pair(
    rows: Sequence[Sequence[object]],
    sheet_name: str,
    field: str,
    canonical_column: int,
    expansion_column: int,
) -> Tuple[List[TaxonomyEntry], int]:
    """Read one canonical/expansion column pair as independent vocabulary.

    The two Sales Category column pairs live on one sheet but are not nested: a row can state an
    LV2 value with no LV1 value beside it, so LV2 parentage is never inferred from row adjacency.
    """
    entries: List[TaxonomyEntry] = []
    blank_expansion_terms = 0
    seen_canonical: Dict[str, int] = {}

    for source_row in range(HEADER_ROW + 1, len(rows) + 1):
        row = rows[source_row - 1]
        raw_canonical = _cell(row, canonical_column)
        raw_expansion = _cell(row, expansion_column)
        normalized_canonical = normalize_query_text(raw_canonical)

        if not normalized_canonical:
            if normalize_query_text(raw_expansion):
                raise SearchTaxonomyError(
                    f"{sheet_name} row {source_row}: {field} states expansion terms with no "
                    "canonical value; the Authority must not leave an expansion list unowned"
                )
            continue

        if normalized_canonical in seen_canonical:
            raise SearchTaxonomyError(
                f"{sheet_name} row {source_row}: {field} canonical value "
                f"{str(raw_canonical)!r} repeats row {seen_canonical[normalized_canonical]}; "
                "a canonical value must be stated once"
            )
        seen_canonical[normalized_canonical] = source_row

        canonical_display = str(raw_canonical)
        entries.append(
            TaxonomyEntry(
                field=field,
                canonical_value=canonical_display,
                normalized_canonical=normalized_canonical,
                alias_value=canonical_display,
                normalized_alias=normalized_canonical,
                match_type=MATCH_TYPE_CANONICAL,
                source_sheet=sheet_name,
                source_row=source_row,
            )
        )

        if raw_expansion is None or not str(raw_expansion):
            continue
        for term in EXPANSION_SEPARATOR_RE.split(str(raw_expansion)):
            normalized_term = normalize_query_text(term)
            if not normalized_term:
                # Trailing separators and padding are workbook punctuation, not vocabulary. They
                # are counted so a reviewer can see them, and dropped so they cannot become an
                # alias that matches every query.
                blank_expansion_terms += 1
                continue
            entries.append(
                TaxonomyEntry(
                    field=field,
                    canonical_value=canonical_display,
                    normalized_canonical=normalized_canonical,
                    alias_value=term.strip(),
                    normalized_alias=normalized_term,
                    match_type=MATCH_TYPE_EXPANSION,
                    source_sheet=sheet_name,
                    source_row=source_row,
                )
            )

    return entries, blank_expansion_terms


def _preferred_entries(entries: Sequence[TaxonomyEntry]) -> Tuple[TaxonomyEntry, ...]:
    """Within one field, a canonical name outranks another row's expansion list.

    A canonical value is the Authority's own primary statement of that value, so it must resolve to
    itself even when a neighbouring row also lists the same text as an expansion term. This is a
    structural rule about what kind of statement wins, not row order and not similarity: two
    *canonical* values colliding inside one field stays ambiguous, and so does any collision that
    crosses fields.
    """
    preferred: List[TaxonomyEntry] = []
    for field in sorted({entry.field for entry in entries}):
        in_field = [entry for entry in entries if entry.field == field]
        canonical = [entry for entry in in_field if entry.match_type == MATCH_TYPE_CANONICAL]
        preferred.extend(canonical or in_field)
    return tuple(preferred)


def _within_edit_distance_one(left: str, right: str) -> bool:
    """True when one substitution, insertion or deletion turns ``left`` into ``right``."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(1 for a, b in zip(left, right) if a != b) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = offset = 0
    while index < len(shorter):
        if shorter[index] != longer[index + offset]:
            if offset:
                return False
            offset = 1
            continue
        index += 1
    return True


def _sheet_rows(
    sheets: Mapping[str, List[List[object]]], sheet_name: str, workbook_path: Path
) -> List[List[object]]:
    if sheet_name not in sheets:
        raise SearchTaxonomyError(
            f"taxonomy workbook {workbook_path} has no sheet named {sheet_name!r}; "
            f"available sheets: {sorted(sheets)}"
        )
    rows = sheets[sheet_name]
    if len(rows) <= HEADER_ROW:
        raise SearchTaxonomyError(
            f"taxonomy workbook {workbook_path} sheet {sheet_name!r} has no vocabulary rows"
        )
    return rows


def _assert_headers(
    rows: Sequence[Sequence[object]],
    sheet_name: str,
    expected_headers: Tuple[str, ...],
    workbook_path: Path,
) -> None:
    actual = _trimmed_headers(rows[HEADER_ROW - 1])
    if actual != list(expected_headers):
        raise SearchTaxonomyError(
            f"taxonomy workbook {workbook_path} sheet {sheet_name!r} header mismatch: expected "
            f"{list(expected_headers)}; actual {actual}"
        )


def _trimmed_headers(row: Sequence[object]) -> List[str]:
    headers = [str(value).strip() if value is not None else "" for value in row]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def _cell(row: Sequence[object], column: int) -> object:
    return row[column] if column < len(row) else None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
