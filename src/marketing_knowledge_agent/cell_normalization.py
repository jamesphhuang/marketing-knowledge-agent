"""Pure, merge-aware normalization for transient Google source cells."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Union

from .sheets_contracts import (
    CellData,
    GoogleValue,
    GridRange,
    SheetSnapshot,
    SpreadsheetSnapshot,
)

NormalizedScalar = Union[str, int, float, bool]


class FieldValueKind(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class ValueSource(str, Enum):
    BLANK = "blank"
    FORMATTED_VALUE = "formatted_value"
    EFFECTIVE_VALUE = "effective_value"
    EFFECTIVE_VALUE_TEXT_FALLBACK = "effective_value_text_fallback"


class InheritanceReason(str, Enum):
    LOCAL = "local"
    MERGE_ANCHOR = "merge_anchor"
    MERGED_RANGE = "merged_range"
    MERGE_INHERITANCE_DISALLOWED = "merge_inheritance_disallowed"


class CellNormalizationError(ValueError):
    """Stable, value-redacted normalization failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FieldContract:
    """Source-field mechanics only; no business or governance semantics."""

    field_name: str
    value_kind: FieldValueKind
    source_column_index: int
    merge_inheritance_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.field_name:
            raise CellNormalizationError("FIELD_CONTRACT_NAME_MISSING")
        if not isinstance(self.value_kind, FieldValueKind):
            raise CellNormalizationError("FIELD_CONTRACT_VALUE_KIND_INVALID")
        if self.source_column_index < 0:
            raise CellNormalizationError("FIELD_CONTRACT_COLUMN_INVALID")
        if not isinstance(self.merge_inheritance_allowed, bool):
            raise CellNormalizationError("FIELD_CONTRACT_MERGE_FLAG_INVALID")


@dataclass(frozen=True)
class SourceLineage:
    spreadsheet_id: str
    sheet_id: int
    sheet_title: str
    sheet_hidden: bool
    source_row_index: int
    source_column_index: int
    source_fingerprint: Optional[str] = None
    sync_batch_id: Optional[str] = None

    @property
    def source_coordinate(self) -> Tuple[int, int]:
        return (self.source_row_index, self.source_column_index)


@dataclass(frozen=True)
class SourceFieldLineage:
    field_name: str
    target_row_index: int
    target_column_index: int
    value_row_index: int
    value_column_index: int
    merge_anchor_row_index: Optional[int]
    merge_anchor_column_index: Optional[int]
    merge_range: Optional[GridRange]
    inherited_from_merge: bool
    inheritance_reason: InheritanceReason

    @property
    def target_coordinate(self) -> Tuple[int, int]:
        return (self.target_row_index, self.target_column_index)

    @property
    def value_coordinate(self) -> Tuple[int, int]:
        return (self.value_row_index, self.value_column_index)

    @property
    def merge_anchor_coordinate(self) -> Optional[Tuple[int, int]]:
        if self.merge_anchor_row_index is None or self.merge_anchor_column_index is None:
            return None
        return (self.merge_anchor_row_index, self.merge_anchor_column_index)


@dataclass(frozen=True, repr=False)
class ResolvedCellValue:
    """Transient value view retaining both target and value-source CellData."""

    normalized_value: Optional[NormalizedScalar]
    display_value: Optional[str]
    value_source: ValueSource
    source_was_formula: bool
    source_cell: Optional[CellData]
    value_cell: Optional[CellData]
    field_contract: FieldContract
    lineage: SourceLineage
    field_lineage: SourceFieldLineage

    def __repr__(self) -> str:
        return (
            "ResolvedCellValue("
            f"field_name={self.field_contract.field_name!r}, "
            f"sheet_id={self.lineage.sheet_id}, "
            f"source_coordinate={self.lineage.source_coordinate!r}, "
            f"value_source={self.value_source.value!r}, "
            "content=<redacted>)"
        )


def normalize_source_cell(
    snapshot: SpreadsheetSnapshot,
    *,
    sheet_id: int,
    source_row_index: int,
    field_contract: FieldContract,
    source_fingerprint: Optional[str] = None,
    sync_batch_id: Optional[str] = None,
) -> ResolvedCellValue:
    """Resolve one transient source cell without fill-down or side effects."""

    if not isinstance(snapshot, SpreadsheetSnapshot):
        raise CellNormalizationError("SPREADSHEET_SNAPSHOT_TYPE_INVALID")
    if not isinstance(field_contract, FieldContract):
        raise CellNormalizationError("FIELD_CONTRACT_TYPE_INVALID")
    if source_fingerprint == "":
        raise CellNormalizationError("SOURCE_FINGERPRINT_EMPTY")
    if sync_batch_id == "":
        raise CellNormalizationError("SYNC_BATCH_ID_EMPTY")

    sheet = _sheet_by_id(snapshot, sheet_id)
    source_column_index = field_contract.source_column_index
    if (
        source_row_index < 0
        or source_row_index >= sheet.row_count
        or source_column_index >= sheet.column_count
    ):
        raise CellNormalizationError("SOURCE_CELL_COORDINATE_OUT_OF_BOUNDS")

    cells_by_coordinate = {
        (cell.row_index, cell.column_index): cell for cell in sheet.cells
    }
    target_coordinate = (source_row_index, source_column_index)
    source_cell = cells_by_coordinate.get(target_coordinate)
    merge_range = _covering_merge(sheet, target_coordinate)

    value_cell = source_cell
    value_coordinate = target_coordinate
    merge_anchor_coordinate: Optional[Tuple[int, int]] = None
    inherited_from_merge = False
    inheritance_reason = InheritanceReason.LOCAL

    if merge_range is not None:
        merge_anchor_coordinate = (
            merge_range.start_row_index,
            merge_range.start_column_index,
        )
        if target_coordinate == merge_anchor_coordinate:
            inheritance_reason = InheritanceReason.MERGE_ANCHOR
        else:
            if _cell_has_source_value(source_cell):
                raise CellNormalizationError("MERGE_COVERED_CELL_SOURCE_CONFLICT")
            if field_contract.merge_inheritance_allowed:
                value_coordinate = merge_anchor_coordinate
                value_cell = cells_by_coordinate.get(merge_anchor_coordinate)
                inherited_from_merge = True
                inheritance_reason = InheritanceReason.MERGED_RANGE
            else:
                inheritance_reason = InheritanceReason.MERGE_INHERITANCE_DISALLOWED

    normalized_value, display_value, value_source, source_was_formula = _resolve_value(
        value_cell,
        field_contract.value_kind,
    )

    return ResolvedCellValue(
        normalized_value=normalized_value,
        display_value=display_value,
        value_source=value_source,
        source_was_formula=source_was_formula,
        source_cell=source_cell,
        value_cell=value_cell,
        field_contract=field_contract,
        lineage=SourceLineage(
            spreadsheet_id=snapshot.spreadsheet_id,
            sheet_id=sheet.sheet_id,
            sheet_title=sheet.title,
            sheet_hidden=sheet.hidden,
            source_row_index=source_row_index,
            source_column_index=source_column_index,
            source_fingerprint=source_fingerprint,
            sync_batch_id=sync_batch_id,
        ),
        field_lineage=SourceFieldLineage(
            field_name=field_contract.field_name,
            target_row_index=source_row_index,
            target_column_index=source_column_index,
            value_row_index=value_coordinate[0],
            value_column_index=value_coordinate[1],
            merge_anchor_row_index=(
                merge_anchor_coordinate[0] if merge_anchor_coordinate is not None else None
            ),
            merge_anchor_column_index=(
                merge_anchor_coordinate[1] if merge_anchor_coordinate is not None else None
            ),
            merge_range=merge_range,
            inherited_from_merge=inherited_from_merge,
            inheritance_reason=inheritance_reason,
        ),
    )


def _sheet_by_id(snapshot: SpreadsheetSnapshot, sheet_id: int) -> SheetSnapshot:
    for sheet in snapshot.sheets:
        if sheet.sheet_id == sheet_id:
            return sheet
    raise CellNormalizationError("SOURCE_SHEET_NOT_FOUND")


def _covering_merge(
    sheet: SheetSnapshot,
    coordinate: Tuple[int, int],
) -> Optional[GridRange]:
    row_index, column_index = coordinate
    for merge_range in sheet.merges:
        if (
            merge_range.start_row_index <= row_index < merge_range.end_row_index
            and merge_range.start_column_index
            <= column_index
            < merge_range.end_column_index
        ):
            return merge_range
    return None


def _cell_has_source_value(cell: Optional[CellData]) -> bool:
    if cell is None:
        return False
    return any(
        (
            cell.formatted_value is not None,
            cell.effective_value is not None,
            cell.user_entered_value is not None,
        )
    )


def _resolve_value(
    cell: Optional[CellData],
    value_kind: FieldValueKind,
) -> Tuple[Optional[NormalizedScalar], Optional[str], ValueSource, bool]:
    if cell is None:
        return None, None, ValueSource.BLANK, False

    effective = cell.effective_value
    source_was_formula = bool(
        cell.user_entered_value is not None
        and cell.user_entered_value.formula_value is not None
    )
    if effective is not None and effective.error_value is not None:
        raise CellNormalizationError("CELL_EFFECTIVE_ERROR")
    if source_was_formula and effective is None:
        raise CellNormalizationError("CELL_FORMULA_EFFECTIVE_VALUE_MISSING")

    _validate_checkbox_contract(cell, value_kind)

    if value_kind is FieldValueKind.TEXT:
        if cell.formatted_value is not None:
            return (
                cell.formatted_value,
                cell.formatted_value,
                ValueSource.FORMATTED_VALUE,
                source_was_formula,
            )
        if effective is None:
            return None, None, ValueSource.BLANK, source_was_formula
        fallback = _effective_value_as_text(effective)
        return (
            fallback,
            fallback,
            ValueSource.EFFECTIVE_VALUE_TEXT_FALLBACK,
            source_was_formula,
        )

    if effective is None:
        if cell.formatted_value not in (None, ""):
            raise CellNormalizationError("CELL_EFFECTIVE_VALUE_MISSING")
        return None, cell.formatted_value, ValueSource.BLANK, source_was_formula

    if value_kind in (FieldValueKind.NUMBER, FieldValueKind.DATE):
        if effective.number_value is None:
            raise CellNormalizationError("CELL_EFFECTIVE_TYPE_MISMATCH")
        normalized_value: NormalizedScalar = effective.number_value
    elif value_kind is FieldValueKind.BOOLEAN:
        if effective.bool_value is None:
            raise CellNormalizationError("CELL_EFFECTIVE_TYPE_MISMATCH")
        normalized_value = effective.bool_value
    else:  # Defensive against invalid objects bypassing FieldContract construction.
        raise CellNormalizationError("FIELD_CONTRACT_VALUE_KIND_INVALID")

    return (
        normalized_value,
        cell.formatted_value,
        ValueSource.EFFECTIVE_VALUE,
        source_was_formula,
    )


def _validate_checkbox_contract(cell: CellData, value_kind: FieldValueKind) -> None:
    if cell.data_validation is None:
        return
    validation_type = cell.data_validation.condition.condition_type
    if value_kind is FieldValueKind.BOOLEAN:
        if validation_type != "BOOLEAN":
            raise CellNormalizationError("CELL_BOOLEAN_VALIDATION_TYPE_MISMATCH")
    elif validation_type == "BOOLEAN":
        raise CellNormalizationError("CELL_BOOLEAN_VALIDATION_TYPE_MISMATCH")


def _effective_value_as_text(effective: GoogleValue) -> str:
    if effective.string_value is not None:
        return effective.string_value
    if effective.number_value is not None:
        return str(effective.number_value)
    if effective.bool_value is not None:
        return "TRUE" if effective.bool_value else "FALSE"
    raise CellNormalizationError("CELL_EFFECTIVE_TYPE_MISMATCH")


__all__ = [
    "CellNormalizationError",
    "FieldContract",
    "FieldValueKind",
    "InheritanceReason",
    "ResolvedCellValue",
    "SourceFieldLineage",
    "SourceLineage",
    "ValueSource",
    "normalize_source_cell",
]
