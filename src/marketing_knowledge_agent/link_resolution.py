"""Pure extraction of raw link candidates from eligible Content Asset cells."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .cell_normalization import (
    FieldValueKind,
    ResolvedCellValue,
    SourceFieldLineage,
    SourceLineage,
)


_LITERAL_HTTP_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_FORMULA_FUNCTION = re.compile(r"\s*=\s*([A-Za-z_]+)")


class AssetSourceSlot(str, Enum):
    ARTICLE = "article"
    VIDEO = "video"
    PODCAST = "podcast"
    NEWS = "news"


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
    "AssetSourceSlot",
    "EligibleAssetLinkCell",
    "LinkCandidate",
    "LinkExtractionError",
    "LinkSource",
    "extract_link_candidates",
]
