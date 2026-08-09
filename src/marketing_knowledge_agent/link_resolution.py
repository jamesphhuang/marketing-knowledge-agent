"""Pure extraction of raw link candidates from eligible Content Asset cells."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .canonical_models import AssetType, BrandId, ContentAssetKey
from .cell_normalization import (
    FieldValueKind,
    ResolvedCellValue,
    SourceFieldLineage,
    SourceLineage,
)

if TYPE_CHECKING:
    from .url_safety import CanonicalURL, URLValidationResult


_LITERAL_HTTP_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_FORMULA_FUNCTION = re.compile(r"\s*=\s*([A-Za-z_]+)")


class AssetSourceSlot(str, Enum):
    ARTICLE = "article"
    VIDEO = "video"
    PODCAST = "podcast"
    NEWS = "news"


_ASSET_TYPE_BY_SOURCE_SLOT = {
    AssetSourceSlot.ARTICLE: AssetType.ARTICLE,
    AssetSourceSlot.VIDEO: AssetType.VIDEO,
    AssetSourceSlot.PODCAST: AssetType.PODCAST,
    AssetSourceSlot.NEWS: AssetType.NEWS,
}
_ASSET_SOURCE_SLOT_BY_TYPE = {
    asset_type: source_slot
    for source_slot, asset_type in _ASSET_TYPE_BY_SOURCE_SLOT.items()
}
_ASSET_SOURCE_COLUMNS = {
    AssetSourceSlot.ARTICLE: 7,
    AssetSourceSlot.VIDEO: 8,
    AssetSourceSlot.PODCAST: 9,
    AssetSourceSlot.NEWS: 10,
}


class LinkSource(str, Enum):
    RICH_TEXT = "rich_text"
    CELL_HYPERLINK = "cell_hyperlink"
    HYPERLINK_FORMULA = "hyperlink_formula"
    LITERAL_TEXT = "literal_text"


class AssetResolutionStatus(str, Enum):
    INCOMPLETE = "incomplete"
    RESOLVED_CANDIDATE = "resolved_candidate"
    NEEDS_REVIEW = "needs_review"


class LinkExtractionError(ValueError):
    """Stable extraction issue that never reflects source content."""

    def __init__(
        self,
        code: str,
        *,
        source: Optional[LinkSource] = None,
        run_ordinal: Optional[int] = None,
    ) -> None:
        self.code = code
        self.source = source
        self.run_ordinal = run_ordinal
        super().__init__(code)


class AssetResolutionError(ValueError):
    """Stable, payload-free failure at the WP8 resolution boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class EligibleAssetLinkCell:
    """A WP3 cell proven to occupy one frozen Content Asset source slot."""

    resolved_cell: ResolvedCellValue
    asset_source_slot: AssetSourceSlot

    def __post_init__(self) -> None:
        if type(self.resolved_cell) is not ResolvedCellValue:
            raise LinkExtractionError("RESOLVED_CELL_VALUE_REQUIRED")
        if type(self.asset_source_slot) is not AssetSourceSlot:
            raise LinkExtractionError("ASSET_SOURCE_SLOT_INVALID")

        resolved = self.resolved_cell
        expected_column = _ASSET_SOURCE_COLUMNS[self.asset_source_slot]
        if (
            resolved.field_contract.value_kind is not FieldValueKind.TEXT
            or resolved.field_contract.source_column_index != expected_column
            or resolved.lineage.source_column_index != expected_column
            or resolved.field_lineage.target_column_index != expected_column
        ):
            raise LinkExtractionError("ASSET_SOURCE_SLOT_COLUMN_MISMATCH")
        if (
            resolved.field_lineage.field_name != resolved.field_contract.field_name
            or resolved.field_lineage.target_coordinate
            != resolved.lineage.source_coordinate
            or (
                resolved.source_cell is not None
                and (
                    resolved.source_cell.row_index,
                    resolved.source_cell.column_index,
                )
                != resolved.field_lineage.target_coordinate
            )
            or (
                resolved.value_cell is not None
                and (
                    resolved.value_cell.row_index,
                    resolved.value_cell.column_index,
                )
                != resolved.field_lineage.value_coordinate
            )
        ):
            raise LinkExtractionError("ASSET_SOURCE_LINEAGE_INVALID")

    def __repr__(self) -> str:
        return (
            "EligibleAssetLinkCell("
            f"asset_source_slot={self.asset_source_slot.value!r}, "
            f"sheet_id={self.resolved_cell.lineage.sheet_id}, "
            f"source_coordinate={self.resolved_cell.lineage.source_coordinate!r}, "
            "content=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class LinkCandidate:
    raw_url: str
    source: LinkSource
    asset_source_slot: AssetSourceSlot
    lineage: SourceLineage
    field_lineage: SourceFieldLineage
    run_start_index: Optional[int] = None
    run_ordinal: Optional[int] = None

    def __post_init__(self) -> None:
        if type(self.raw_url) is not str or not self.raw_url.strip():
            raise LinkExtractionError("LINK_CANDIDATE_URI_INVALID", source=self.source)
        rich_text = self.source is LinkSource.RICH_TEXT
        has_run_provenance = (
            self.run_start_index is not None and self.run_ordinal is not None
        )
        if rich_text != has_run_provenance:
            raise LinkExtractionError("LINK_CANDIDATE_RUN_PROVENANCE_INVALID")

    def __repr__(self) -> str:
        return (
            "LinkCandidate("
            f"source={self.source.value!r}, "
            f"asset_source_slot={self.asset_source_slot.value!r}, "
            f"sheet_id={self.lineage.sheet_id}, "
            f"source_coordinate={self.lineage.source_coordinate!r}, "
            f"run_start_index={self.run_start_index!r}, "
            f"run_ordinal={self.run_ordinal!r}, "
            "raw_url=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class ContentAssetCandidate:
    """One safe canonical URL group under a single v1 logical asset key."""

    asset_key: ContentAssetKey
    brand_id: BrandId
    title: Optional[str]
    canonical_url: CanonicalURL
    provenance_occurrences: Tuple[URLValidationResult, ...]
    lineage: SourceLineage
    field_lineage: SourceFieldLineage

    def __post_init__(self) -> None:
        from .url_safety import CanonicalURL, URLValidationResult

        _validate_resolution_identity(self.asset_key, self.brand_id)
        _validate_resolution_title(self.title)
        source_slot = _validate_resolution_lineage(
            self.asset_key,
            self.lineage,
            self.field_lineage,
        )
        if type(self.canonical_url) is not CanonicalURL:
            raise AssetResolutionError("CANONICAL_URL_REQUIRED")
        if (
            type(self.provenance_occurrences) is not tuple
            or not self.provenance_occurrences
        ):
            raise AssetResolutionError("ASSET_PROVENANCE_REQUIRED")
        for occurrence in self.provenance_occurrences:
            if type(occurrence) is not URLValidationResult:
                raise AssetResolutionError("URL_VALIDATION_RESULT_REQUIRED")
            _validate_validation_result_state(occurrence)
            if (
                occurrence.canonical_url != self.canonical_url
                or occurrence.asset_source_slot is not source_slot
                or occurrence.lineage != self.lineage
                or occurrence.field_lineage != self.field_lineage
            ):
                raise AssetResolutionError("ASSET_PROVENANCE_MISMATCH")

    def __repr__(self) -> str:
        return (
            "ContentAssetCandidate("
            f"asset_key={str(self.asset_key)!r}, "
            f"brand_id={str(self.brand_id)!r}, "
            f"canonical_url={self.canonical_url.value!r}, "
            f"provenance_count={len(self.provenance_occurrences)}, "
            "title=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class AssetResolution:
    """A staging-only 0/1/2+ resolution for one logical Content Asset."""

    asset_key: ContentAssetKey
    brand_id: BrandId
    title: Optional[str]
    status: AssetResolutionStatus
    candidates: Tuple[ContentAssetCandidate, ...]
    rejected_occurrences: Tuple[URLValidationResult, ...]
    lineage: SourceLineage
    field_lineage: SourceFieldLineage

    def __post_init__(self) -> None:
        from .url_safety import URLRejectionCode, URLValidationResult

        _validate_resolution_identity(self.asset_key, self.brand_id)
        _validate_resolution_title(self.title)
        source_slot = _validate_resolution_lineage(
            self.asset_key,
            self.lineage,
            self.field_lineage,
        )
        if type(self.status) is not AssetResolutionStatus:
            raise AssetResolutionError("ASSET_RESOLUTION_STATUS_INVALID")
        if type(self.candidates) is not tuple:
            raise AssetResolutionError("ASSET_CANDIDATES_TUPLE_REQUIRED")
        if type(self.rejected_occurrences) is not tuple:
            raise AssetResolutionError("URL_VALIDATION_RESULTS_TUPLE_REQUIRED")

        for candidate in self.candidates:
            if type(candidate) is not ContentAssetCandidate:
                raise AssetResolutionError("CONTENT_ASSET_CANDIDATE_REQUIRED")
            if (
                candidate.asset_key != self.asset_key
                or candidate.brand_id != self.brand_id
                or candidate.title != self.title
                or candidate.lineage != self.lineage
                or candidate.field_lineage != self.field_lineage
            ):
                raise AssetResolutionError("CONTENT_ASSET_CANDIDATE_MISMATCH")

        canonical_values = [
            candidate.canonical_url.value for candidate in self.candidates
        ]
        if len(set(canonical_values)) != len(canonical_values):
            raise AssetResolutionError(
                "ASSET_RESOLUTION_CANONICAL_GROUP_DUPLICATE"
            )

        for occurrence in self.rejected_occurrences:
            if type(occurrence) is not URLValidationResult:
                raise AssetResolutionError("URL_VALIDATION_RESULT_REQUIRED")
            _validate_validation_result_state(occurrence)
            if (
                occurrence.canonical_url is not None
                or type(occurrence.rejection_code) is not URLRejectionCode
                or occurrence.asset_source_slot is not source_slot
                or occurrence.lineage != self.lineage
                or occurrence.field_lineage != self.field_lineage
            ):
                raise AssetResolutionError("ASSET_REJECTED_PROVENANCE_MISMATCH")

        candidate_count = len(self.candidates)
        if self.status is AssetResolutionStatus.INCOMPLETE:
            valid_state = candidate_count == 0 and self.title is not None
        elif self.status is AssetResolutionStatus.RESOLVED_CANDIDATE:
            valid_state = candidate_count == 1
        else:
            valid_state = candidate_count >= 2 or (
                candidate_count == 0
                and self.title is None
                and bool(self.rejected_occurrences)
            )
        if not valid_state:
            raise AssetResolutionError("ASSET_RESOLUTION_STATE_INVALID")

    def __repr__(self) -> str:
        return (
            "AssetResolution("
            f"asset_key={str(self.asset_key)!r}, "
            f"brand_id={str(self.brand_id)!r}, "
            f"status={self.status.value!r}, "
            f"candidate_count={len(self.candidates)}, "
            f"rejected_count={len(self.rejected_occurrences)}, "
            f"sheet_id={self.lineage.sheet_id!r}, "
            f"source_coordinate={self.lineage.source_coordinate!r}, "
            "title=<redacted>)"
        )


def extract_link_candidates(eligible_cell: EligibleAssetLinkCell) -> Tuple[LinkCandidate, ...]:
    """Return every raw candidate in frozen source-priority order."""

    if type(eligible_cell) is not EligibleAssetLinkCell:
        raise LinkExtractionError("ELIGIBLE_ASSET_LINK_CELL_REQUIRED")

    resolved = eligible_cell.resolved_cell
    cell = resolved.value_cell
    if cell is None:
        return ()

    candidates = []
    for run_ordinal, run in enumerate(cell.text_format_runs):
        if run.link is None:
            continue
        if not run.link.uri.strip():
            raise LinkExtractionError(
                "RICH_TEXT_LINK_URI_INVALID",
                source=LinkSource.RICH_TEXT,
                run_ordinal=run_ordinal,
            )
        candidates.append(
            _candidate(
                eligible_cell,
                raw_url=run.link.uri,
                source=LinkSource.RICH_TEXT,
                run_start_index=run.start_index,
                run_ordinal=run_ordinal,
            )
        )

    if cell.hyperlink is not None:
        if not cell.hyperlink.strip():
            raise LinkExtractionError(
                "CELL_HYPERLINK_URI_INVALID",
                source=LinkSource.CELL_HYPERLINK,
            )
        candidates.append(
            _candidate(
                eligible_cell,
                raw_url=cell.hyperlink,
                source=LinkSource.CELL_HYPERLINK,
            )
        )

    entered = cell.user_entered_value
    formula = entered.formula_value if entered is not None else None
    if formula is not None:
        formula_url = _parse_hyperlink_first_argument(formula)
        if formula_url is not None:
            candidates.append(
                _candidate(
                    eligible_cell,
                    raw_url=formula_url,
                    source=LinkSource.HYPERLINK_FORMULA,
                )
            )

    display_value = resolved.display_value
    if (
        type(display_value) is str
        and _LITERAL_HTTP_URL.fullmatch(display_value) is not None
    ):
        candidates.append(
            _candidate(
                eligible_cell,
                raw_url=display_value,
                source=LinkSource.LITERAL_TEXT,
            )
        )

    return tuple(candidates)


def resolve_content_asset(
    *,
    asset_key: ContentAssetKey,
    brand_id: BrandId,
    normalized_title: Optional[str],
    lineage: SourceLineage,
    field_lineage: SourceFieldLineage,
    candidates: Tuple[LinkCandidate, ...],
    validation_results: Tuple[URLValidationResult, ...],
) -> Optional[AssetResolution]:
    """Resolve one WP8 logical asset from complete WP6/WP7 typed inputs."""

    _validate_resolution_identity(asset_key, brand_id)
    _validate_resolution_title(normalized_title)
    _validate_resolution_lineage(asset_key, lineage, field_lineage)
    pairs = _pair_candidates_and_results(
        asset_key=asset_key,
        lineage=lineage,
        field_lineage=field_lineage,
        candidates=candidates,
        validation_results=validation_results,
    )

    if normalized_title is None and not candidates:
        return None

    grouped_results: List[List[URLValidationResult]] = []
    grouped_urls: List[CanonicalURL] = []
    group_indexes: Dict[str, int] = {}
    rejected_occurrences: List[URLValidationResult] = []
    for _, result in pairs:
        if result.canonical_url is None:
            rejected_occurrences.append(result)
            continue
        canonical_value = result.canonical_url.value
        group_index = group_indexes.get(canonical_value)
        if group_index is None:
            group_indexes[canonical_value] = len(grouped_results)
            grouped_urls.append(result.canonical_url)
            grouped_results.append([])
            group_index = len(grouped_results) - 1
        grouped_results[group_index].append(result)

    canonical_candidates = tuple(
        ContentAssetCandidate(
            asset_key=asset_key,
            brand_id=brand_id,
            title=normalized_title,
            canonical_url=canonical_url,
            provenance_occurrences=tuple(occurrences),
            lineage=lineage,
            field_lineage=field_lineage,
        )
        for canonical_url, occurrences in zip(grouped_urls, grouped_results)
    )
    distinct_safe_count = len(canonical_candidates)
    if distinct_safe_count == 0:
        status = (
            AssetResolutionStatus.INCOMPLETE
            if normalized_title is not None
            else AssetResolutionStatus.NEEDS_REVIEW
        )
    elif distinct_safe_count == 1:
        status = AssetResolutionStatus.RESOLVED_CANDIDATE
    else:
        status = AssetResolutionStatus.NEEDS_REVIEW

    return AssetResolution(
        asset_key=asset_key,
        brand_id=brand_id,
        title=normalized_title,
        status=status,
        candidates=canonical_candidates,
        rejected_occurrences=tuple(rejected_occurrences),
        lineage=lineage,
        field_lineage=field_lineage,
    )


def _validate_resolution_identity(
    asset_key: ContentAssetKey,
    brand_id: BrandId,
) -> None:
    if type(asset_key) is not ContentAssetKey:
        raise AssetResolutionError("CONTENT_ASSET_KEY_REQUIRED")
    if type(brand_id) is not BrandId:
        raise AssetResolutionError("BRAND_ID_REQUIRED")


def _validate_resolution_title(title: Optional[str]) -> None:
    if title is None:
        return
    if type(title) is not str:
        raise AssetResolutionError("ASSET_TITLE_TEXT_REQUIRED")
    if not title.strip():
        raise AssetResolutionError("ASSET_TITLE_NOT_NORMALIZED")


def _validate_resolution_lineage(
    asset_key: ContentAssetKey,
    lineage: SourceLineage,
    field_lineage: SourceFieldLineage,
) -> AssetSourceSlot:
    if (
        type(lineage) is not SourceLineage
        or type(field_lineage) is not SourceFieldLineage
    ):
        raise AssetResolutionError("ASSET_SOURCE_LINEAGE_REQUIRED")
    source_slot = _ASSET_SOURCE_SLOT_BY_TYPE[asset_key.asset_type]
    expected_column = _ASSET_SOURCE_COLUMNS[source_slot]
    if (
        lineage.source_column_index != expected_column
        or field_lineage.target_column_index != expected_column
    ):
        raise AssetResolutionError("ASSET_SOURCE_SLOT_MISMATCH")
    if field_lineage.target_coordinate != lineage.source_coordinate:
        raise AssetResolutionError("ASSET_SOURCE_LINEAGE_INVALID")
    return source_slot


def _pair_candidates_and_results(
    *,
    asset_key: ContentAssetKey,
    lineage: SourceLineage,
    field_lineage: SourceFieldLineage,
    candidates: Tuple[LinkCandidate, ...],
    validation_results: Tuple[URLValidationResult, ...],
) -> Tuple[Tuple[LinkCandidate, URLValidationResult], ...]:
    from .url_safety import URLValidationResult

    if type(candidates) is not tuple:
        raise AssetResolutionError("ASSET_CANDIDATES_TUPLE_REQUIRED")
    if type(validation_results) is not tuple:
        raise AssetResolutionError("URL_VALIDATION_RESULTS_TUPLE_REQUIRED")

    source_slot = _ASSET_SOURCE_SLOT_BY_TYPE[asset_key.asset_type]
    candidate_provenance = []
    for candidate in candidates:
        if type(candidate) is not LinkCandidate:
            raise AssetResolutionError("LINK_CANDIDATE_REQUIRED")
        if (
            type(candidate.source) is not LinkSource
            or type(candidate.asset_source_slot) is not AssetSourceSlot
        ):
            raise AssetResolutionError("LINK_CANDIDATE_PROVENANCE_INVALID")
        if candidate.asset_source_slot is not source_slot:
            raise AssetResolutionError("ASSET_SOURCE_SLOT_MISMATCH")
        if (
            candidate.lineage != lineage
            or candidate.field_lineage != field_lineage
        ):
            raise AssetResolutionError("ASSET_SOURCE_LINEAGE_MISMATCH")
        provenance = _provenance_key(candidate)
        if provenance in candidate_provenance:
            raise AssetResolutionError("ASSET_CANDIDATE_PROVENANCE_DUPLICATE")
        candidate_provenance.append(provenance)

    result_provenance = []
    for result in validation_results:
        if type(result) is not URLValidationResult:
            raise AssetResolutionError("URL_VALIDATION_RESULT_REQUIRED")
        _validate_validation_result_state(result)
        provenance = _provenance_key(result)
        if provenance in result_provenance:
            raise AssetResolutionError("ASSET_RESULT_PROVENANCE_DUPLICATE")
        result_provenance.append(provenance)

    if len(candidates) != len(validation_results):
        raise AssetResolutionError("ASSET_CANDIDATE_RESULT_COUNT_MISMATCH")

    remaining_results = list(validation_results)
    pairs = []
    for candidate in candidates:
        for result_index, result in enumerate(remaining_results):
            if _provenance_matches(candidate, result):
                pairs.append((candidate, remaining_results.pop(result_index)))
                break
        else:
            raise AssetResolutionError(
                "ASSET_CANDIDATE_RESULT_PROVENANCE_MISMATCH"
            )
    if remaining_results:
        raise AssetResolutionError("ASSET_CANDIDATE_RESULT_PROVENANCE_MISMATCH")
    return tuple(pairs)


def _validate_validation_result_state(result: URLValidationResult) -> None:
    from .url_safety import CanonicalURL, URLRejectionCode

    if result.canonical_url is not None:
        if type(result.canonical_url) is not CanonicalURL:
            raise AssetResolutionError("CANONICAL_URL_REQUIRED")
        if result.rejection_code is not None:
            raise AssetResolutionError("URL_VALIDATION_RESULT_STATE_INVALID")
    elif type(result.rejection_code) is not URLRejectionCode:
        raise AssetResolutionError("URL_VALIDATION_RESULT_STATE_INVALID")
    if (
        type(result.source) is not LinkSource
        or type(result.asset_source_slot) is not AssetSourceSlot
        or type(result.lineage) is not SourceLineage
        or type(result.field_lineage) is not SourceFieldLineage
    ):
        raise AssetResolutionError("URL_VALIDATION_RESULT_PROVENANCE_INVALID")


def _provenance_matches(
    candidate: LinkCandidate,
    result: URLValidationResult,
) -> bool:
    return _provenance_key(candidate) == _provenance_key(result)


def _provenance_key(candidate_or_result) -> tuple:
    return (
        candidate_or_result.source,
        candidate_or_result.asset_source_slot,
        candidate_or_result.lineage,
        candidate_or_result.field_lineage,
        candidate_or_result.run_start_index,
        candidate_or_result.run_ordinal,
    )


def _parse_hyperlink_first_argument(formula: str) -> Optional[str]:
    function = _FORMULA_FUNCTION.match(formula)
    if function is None or function.group(1).casefold() != "hyperlink":
        return None

    index = _skip_whitespace(formula, function.end())
    if index >= len(formula) or formula[index] != "(":
        raise _formula_error("HYPERLINK_FORMULA_MALFORMED")
    index = _skip_whitespace(formula, index + 1)
    if index >= len(formula) or formula[index] != '"':
        raise _formula_error("HYPERLINK_FIRST_ARGUMENT_NOT_STATIC")

    raw_url, index = _parse_formula_string(formula, index)
    if not raw_url:
        raise _formula_error("HYPERLINK_FIRST_ARGUMENT_EMPTY")

    index = _skip_whitespace(formula, index)
    if index < len(formula) and formula[index] == ")":
        if formula[index + 1 :].strip():
            raise _formula_error("HYPERLINK_FORMULA_MALFORMED")
        return raw_url
    if index >= len(formula) or formula[index] != ",":
        raise _formula_error("HYPERLINK_FORMULA_MALFORMED")
    if not _has_valid_display_argument(formula, index + 1):
        raise _formula_error("HYPERLINK_FORMULA_MALFORMED")
    return raw_url


def _parse_formula_string(formula: str, opening_quote_index: int) -> Tuple[str, int]:
    characters = []
    index = opening_quote_index + 1
    while index < len(formula):
        if formula[index] != '"':
            characters.append(formula[index])
            index += 1
        elif index + 1 < len(formula) and formula[index + 1] == '"':
            characters.append('"')
            index += 2
        else:
            return "".join(characters), index + 1
    raise _formula_error("HYPERLINK_FORMULA_MALFORMED")


def _has_valid_display_argument(formula: str, start_index: int) -> bool:
    index = _skip_whitespace(formula, start_index)
    if index >= len(formula) or formula[index] == ")":
        return False

    depth = 0
    in_string = False
    while index < len(formula):
        character = formula[index]
        if in_string:
            if character == '"':
                if index + 1 < len(formula) and formula[index + 1] == '"':
                    index += 2
                    continue
                in_string = False
        elif character == '"':
            in_string = True
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth:
                depth -= 1
            else:
                return not formula[index + 1 :].strip()
        elif character == "," and depth == 0:
            return False
        index += 1
    return False


def _skip_whitespace(value: str, index: int) -> int:
    while index < len(value) and value[index].isspace():
        index += 1
    return index


def _formula_error(code: str) -> LinkExtractionError:
    return LinkExtractionError(code, source=LinkSource.HYPERLINK_FORMULA)


def _candidate(
    eligible_cell: EligibleAssetLinkCell,
    *,
    raw_url: str,
    source: LinkSource,
    run_start_index: Optional[int] = None,
    run_ordinal: Optional[int] = None,
) -> LinkCandidate:
    resolved = eligible_cell.resolved_cell
    return LinkCandidate(
        raw_url=raw_url,
        source=source,
        asset_source_slot=eligible_cell.asset_source_slot,
        lineage=resolved.lineage,
        field_lineage=resolved.field_lineage,
        run_start_index=run_start_index,
        run_ordinal=run_ordinal,
    )


__all__ = [
    "AssetResolution",
    "AssetResolutionError",
    "AssetResolutionStatus",
    "AssetSourceSlot",
    "ContentAssetCandidate",
    "EligibleAssetLinkCell",
    "LinkCandidate",
    "LinkExtractionError",
    "LinkSource",
    "extract_link_candidates",
    "resolve_content_asset",
]
