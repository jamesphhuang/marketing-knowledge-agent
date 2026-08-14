"""Read-only, Google CellData-shaped transport contracts."""

from __future__ import annotations

from typing import ClassVar, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")
if _PYDANTIC_V2:
    from pydantic import ConfigDict, field_validator, model_validator
else:  # pragma: no cover - exercised only with Pydantic 1.x
    from pydantic import root_validator, validator


class _ReadOnlyDTO(BaseModel):
    if _PYDANTIC_V2:
        model_config = ConfigDict(
            frozen=True,
            extra="forbid",
            hide_input_in_errors=True,
        )
    else:  # pragma: no cover - exercised only with Pydantic 1.x

        class Config:
            allow_mutation = False
            extra = "forbid"


class _PayloadSafeDTO(_ReadOnlyDTO):
    """Payload-safe debug rendering; trusted serialization remains unchanged."""

    _safe_repr_fields: ClassVar[Tuple[str, ...]] = ()

    def __repr__(self) -> str:
        details = ", ".join(
            f"{name}={getattr(self, name)!r}" for name in self._safe_repr_fields
        )
        return f"{type(self).__name__}({details})"

    def __str__(self) -> str:
        return repr(self)


class GoogleError(_PayloadSafeDTO):
    _safe_repr_fields = ("error_type",)

    error_type: StrictStr
    message: Optional[StrictStr] = None


class GoogleValue(_PayloadSafeDTO):
    """One branch of the Google Sheets ``ExtendedValue`` union."""

    string_value: Optional[StrictStr] = None
    number_value: Optional[Union[StrictInt, StrictFloat]] = None
    bool_value: Optional[StrictBool] = None
    formula_value: Optional[StrictStr] = None
    error_value: Optional[GoogleError] = None

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def require_exactly_one_value_kind(self):
            _validate_exactly_one_google_value(self.__dict__)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @root_validator(skip_on_failure=True)
        def require_exactly_one_value_kind(cls, values):
            _validate_exactly_one_google_value(values)
            return values


class TextFormatLink(_PayloadSafeDTO):
    uri: StrictStr = Field(..., min_length=1)


class TextFormatRun(_PayloadSafeDTO):
    _safe_repr_fields = ("start_index",)

    start_index: int = Field(..., ge=0)
    link: Optional[TextFormatLink] = None


class ConditionValue(_PayloadSafeDTO):
    relative_date: Optional[StrictStr] = None
    user_entered_value: Optional[StrictStr] = None

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def require_exactly_one_condition_value_kind(self):
            _validate_exactly_one_condition_value(self.__dict__)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @root_validator(skip_on_failure=True)
        def require_exactly_one_condition_value_kind(cls, values):
            _validate_exactly_one_condition_value(values)
            return values


class DataValidationCondition(_PayloadSafeDTO):
    _safe_repr_fields = ("condition_type",)

    condition_type: StrictStr = Field(..., min_length=1)
    values: Tuple[ConditionValue, ...] = ()


class DataValidation(_PayloadSafeDTO):
    condition: DataValidationCondition
    input_message: Optional[StrictStr] = None
    strict: Optional[StrictBool] = None
    show_custom_ui: Optional[StrictBool] = None


class CellData(_PayloadSafeDTO):
    """A source cell without business-value selection or normalization."""

    _safe_repr_fields = ("row_index", "column_index")

    row_index: int = Field(..., ge=0)
    column_index: int = Field(..., ge=0)
    formatted_value: Optional[StrictStr] = None
    effective_value: Optional[GoogleValue] = None
    user_entered_value: Optional[GoogleValue] = None
    hyperlink: Optional[StrictStr] = None
    text_format_runs: Tuple[TextFormatRun, ...] = ()
    data_validation: Optional[DataValidation] = None

    if _PYDANTIC_V2:

        @field_validator("text_format_runs")
        @classmethod
        def require_strictly_increasing_run_offsets(cls, runs):
            return _validate_text_format_runs(runs)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("text_format_runs")
        def require_strictly_increasing_run_offsets(cls, runs):
            return _validate_text_format_runs(runs)


class GridRange(_ReadOnlyDTO):
    """A half-open Google Sheets grid range."""

    sheet_id: int = Field(..., ge=0)
    start_row_index: int = Field(..., ge=0)
    end_row_index: int = Field(..., ge=0)
    start_column_index: int = Field(..., ge=0)
    end_column_index: int = Field(..., ge=0)

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def require_non_empty_half_open_range(self):
            _validate_grid_range(self.__dict__)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @root_validator(skip_on_failure=True)
        def require_non_empty_half_open_range(cls, values):
            _validate_grid_range(values)
            return values


class SheetSnapshot(_PayloadSafeDTO):
    _safe_repr_fields = ("sheet_id", "row_count", "column_count")

    sheet_id: int = Field(..., ge=0)
    title: StrictStr = Field(..., min_length=1)
    hidden: StrictBool = False
    row_count: int = Field(..., gt=0)
    column_count: int = Field(..., gt=0)
    cells: Tuple[CellData, ...] = ()
    merges: Tuple[GridRange, ...] = ()

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def validate_coordinates_and_merges(self):
            _validate_sheet_snapshot(self.__dict__)
            return self

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @root_validator(skip_on_failure=True)
        def validate_coordinates_and_merges(cls, values):
            _validate_sheet_snapshot(values)
            return values


class SpreadsheetSnapshot(_PayloadSafeDTO):
    spreadsheet_id: StrictStr = Field(..., min_length=1)
    sheets: Tuple[SheetSnapshot, ...]

    def __repr__(self) -> str:
        return f"SpreadsheetSnapshot(sheet_count={len(self.sheets)})"

    __str__ = __repr__

    if _PYDANTIC_V2:

        @field_validator("sheets")
        @classmethod
        def require_unique_non_empty_sheets(cls, sheets):
            return _validate_sheets(sheets)

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("sheets")
        def require_unique_non_empty_sheets(cls, sheets):
            return _validate_sheets(sheets)


class SheetsReadRequest(_ReadOnlyDTO):
    spreadsheet_id: StrictStr = Field(..., min_length=1)
    ranges: Tuple[StrictStr, ...]
    fields: Tuple[StrictStr, ...]

    if _PYDANTIC_V2:

        @field_validator("ranges")
        @classmethod
        def require_explicit_non_empty_ranges(cls, values):
            return _validate_selection(values, "SHEETS_READ_RANGES_MISSING")

        @field_validator("fields")
        @classmethod
        def require_explicit_non_empty_fields(cls, values):
            return _validate_selection(values, "SHEETS_READ_FIELDS_MISSING")

    else:  # pragma: no cover - exercised only with Pydantic 1.x

        @validator("ranges")
        def require_explicit_non_empty_ranges(cls, values):
            return _validate_selection(values, "SHEETS_READ_RANGES_MISSING")

        @validator("fields")
        def require_explicit_non_empty_fields(cls, values):
            return _validate_selection(values, "SHEETS_READ_FIELDS_MISSING")


@runtime_checkable
class SheetsReader(Protocol):
    """Injectable read-only boundary for future Sheets adapters."""

    def read(self, request: SheetsReadRequest) -> SpreadsheetSnapshot:
        ...


def _validate_exactly_one_google_value(values: Mapping[str, object]) -> None:
    known_fields = (
        "string_value",
        "number_value",
        "bool_value",
        "formula_value",
        "error_value",
    )
    present = [field for field in known_fields if values.get(field) is not None]
    if len(present) != 1:
        raise ValueError("GOOGLE_VALUE_MULTIPLE_KINDS" if present else "GOOGLE_VALUE_KIND_MISSING")


def _validate_exactly_one_condition_value(values: Mapping[str, object]) -> None:
    present = [
        field
        for field in ("relative_date", "user_entered_value")
        if values.get(field) is not None
    ]
    if len(present) != 1:
        raise ValueError(
            "CONDITION_VALUE_MULTIPLE_KINDS" if present else "CONDITION_VALUE_KIND_MISSING"
        )


def _validate_text_format_runs(runs: Tuple[TextFormatRun, ...]) -> Tuple[TextFormatRun, ...]:
    offsets = [run.start_index for run in runs]
    if offsets != sorted(set(offsets)):
        raise ValueError("TEXT_FORMAT_RUNS_NOT_STRICTLY_ORDERED")
    return runs


def _validate_grid_range(values: Mapping[str, int]) -> None:
    if values["end_row_index"] <= values["start_row_index"]:
        raise ValueError("GRID_RANGE_ROW_BOUNDS_INVALID")
    if values["end_column_index"] <= values["start_column_index"]:
        raise ValueError("GRID_RANGE_COLUMN_BOUNDS_INVALID")


def _validate_sheet_snapshot(values: Mapping[str, object]) -> None:
    sheet_id = values["sheet_id"]
    row_count = values["row_count"]
    column_count = values["column_count"]
    cells = values["cells"]
    merges = values["merges"]

    coordinates = set()
    for cell in cells:
        coordinate = (cell.row_index, cell.column_index)
        if coordinate in coordinates:
            raise ValueError("CELL_COORDINATE_DUPLICATE")
        coordinates.add(coordinate)
        if cell.row_index >= row_count or cell.column_index >= column_count:
            raise ValueError("CELL_COORDINATE_OUT_OF_BOUNDS")

    for merge in merges:
        if merge.sheet_id != sheet_id:
            raise ValueError("MERGE_RANGE_SHEET_ID_MISMATCH")
        if merge.end_row_index > row_count or merge.end_column_index > column_count:
            raise ValueError("MERGE_RANGE_OUT_OF_BOUNDS")

    for index, left in enumerate(merges):
        for right in merges[index + 1 :]:
            if _ranges_overlap(left, right):
                raise ValueError("MERGE_RANGES_OVERLAP")


def _validate_sheets(sheets: Tuple[SheetSnapshot, ...]) -> Tuple[SheetSnapshot, ...]:
    if not sheets:
        raise ValueError("SPREADSHEET_SHEETS_MISSING")
    sheet_ids = [sheet.sheet_id for sheet in sheets]
    if len(sheet_ids) != len(set(sheet_ids)):
        raise ValueError("SPREADSHEET_SHEET_ID_DUPLICATE")
    titles = [sheet.title for sheet in sheets]
    if len(titles) != len(set(titles)):
        raise ValueError("SPREADSHEET_SHEET_TITLE_DUPLICATE")
    return sheets


def _validate_selection(values: Tuple[str, ...], error_code: str) -> Tuple[str, ...]:
    if not values or any(not value for value in values):
        raise ValueError(error_code)
    return values


def _ranges_overlap(left: GridRange, right: GridRange) -> bool:
    rows_overlap = (
        left.start_row_index < right.end_row_index
        and right.start_row_index < left.end_row_index
    )
    columns_overlap = (
        left.start_column_index < right.end_column_index
        and right.start_column_index < left.end_column_index
    )
    return rows_overlap and columns_overlap


__all__ = [
    "CellData",
    "ConditionValue",
    "DataValidation",
    "DataValidationCondition",
    "GoogleError",
    "GoogleValue",
    "GridRange",
    "SheetSnapshot",
    "SheetsReadRequest",
    "SheetsReader",
    "SpreadsheetSnapshot",
    "TextFormatLink",
    "TextFormatRun",
]
