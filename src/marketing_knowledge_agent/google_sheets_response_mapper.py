"""Pure, strict mapping of injected ``spreadsheets.get``-shaped responses."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pydantic import ValidationError

from .google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredRangeCoverageProof,
    ConfiguredReadPlan,
    ConfiguredReadResult,
    ConfiguredSheet,
    CoveredRange,
    _create_configured_read_result,
)
from .sheets_contracts import (
    CellData,
    ConditionValue,
    DataValidation,
    DataValidationCondition,
    GoogleError,
    GoogleValue,
    GridRange,
    SheetSnapshot,
    SpreadsheetSnapshot,
    TextFormatLink,
    TextFormatRun,
)


class GoogleSheetsResponseError(ValueError):
    """Stable payload-safe mapper or coverage failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def map_google_sheets_response(
    response: Mapping[str, object],
    configuration: ConfiguredReadPlan,
) -> ConfiguredReadResult:
    """Map one injected response and return only a coverage-bound result."""

    if not isinstance(configuration, ConfiguredReadPlan):
        _fail("GOOGLE_RESPONSE_CONFIGURATION_INVALID")
    try:
        return _map_response(response, configuration)
    except GoogleSheetsResponseError as error:
        error.__cause__ = None
        error.__context__ = None
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        sanitized_error = GoogleSheetsResponseError("GOOGLE_RESPONSE_INVALID")
    raise sanitized_error from None


def _map_response(
    response: Mapping[str, object],
    configuration: ConfiguredReadPlan,
) -> ConfiguredReadResult:
    root = _mapping(response, "GOOGLE_RESPONSE_SHAPE_UNSUPPORTED")
    _keys(
        root,
        required={"spreadsheetId", "sheets"},
        allowed={"spreadsheetId", "sheets"},
        code="GOOGLE_RESPONSE_SHAPE_UNSUPPORTED",
    )
    spreadsheet_id = _strict_string(
        root["spreadsheetId"], "GOOGLE_RESPONSE_TARGET_INVALID"
    )
    if spreadsheet_id != configuration.spreadsheet_id:
        _fail("GOOGLE_RESPONSE_TARGET_MISMATCH")

    raw_sheets = _array(root["sheets"], "GOOGLE_RESPONSE_SHEETS_INVALID")
    configured_sheets = {sheet.sheet_id: sheet for sheet in configuration.sheets}
    configured_ranges = {
        (value.sheet_id, value.start_row_index, value.start_column_index): value
        for value in configuration.ranges
    }
    seen_sheet_ids: Set[int] = set()
    seen_range_ids: Set[str] = set()
    mapped_sheets: List[SheetSnapshot] = []

    for raw_sheet in raw_sheets:
        sheet = _mapping(raw_sheet, "GOOGLE_RESPONSE_SHEET_INVALID")
        _keys(
            sheet,
            required={"properties", "data", "merges"},
            allowed={"properties", "data", "merges"},
            code="GOOGLE_RESPONSE_SHEET_SHAPE_UNSUPPORTED",
            missing_codes={
                "data": "GOOGLE_RESPONSE_SHEET_DATA_MISSING",
                "merges": "GOOGLE_RESPONSE_SHEET_MERGES_MISSING",
            },
        )
        properties = _mapping(
            sheet["properties"], "GOOGLE_RESPONSE_SHEET_PROPERTIES_INVALID"
        )
        _keys(
            properties,
            required={"sheetId", "title", "gridProperties"},
            allowed={"sheetId", "title", "hidden", "gridProperties"},
            code="GOOGLE_RESPONSE_SHEET_PROPERTIES_INVALID",
        )
        sheet_id = _non_negative_int(
            properties["sheetId"], "GOOGLE_RESPONSE_SHEET_ID_INVALID"
        )
        if sheet_id in seen_sheet_ids:
            _fail("GOOGLE_RESPONSE_SHEET_DUPLICATE")
        expected_sheet = configured_sheets.get(sheet_id)
        if expected_sheet is None:
            _fail("GOOGLE_RESPONSE_SHEET_UNEXPECTED")
        seen_sheet_ids.add(sheet_id)

        title = _strict_string(properties["title"], "GOOGLE_RESPONSE_SHEET_TITLE_INVALID")
        hidden = _optional_bool(properties, "hidden", default=False)
        if title != expected_sheet.title or hidden != expected_sheet.hidden:
            _fail("GOOGLE_RESPONSE_SHEET_MISMATCH")

        grid_properties = _mapping(
            properties["gridProperties"], "GOOGLE_RESPONSE_GRID_PROPERTIES_INVALID"
        )
        _keys(
            grid_properties,
            required={"rowCount", "columnCount"},
            allowed={"rowCount", "columnCount"},
            code="GOOGLE_RESPONSE_GRID_PROPERTIES_INVALID",
        )
        row_count = _positive_int(
            grid_properties["rowCount"], "GOOGLE_RESPONSE_GRID_BOUNDS_INVALID"
        )
        column_count = _positive_int(
            grid_properties["columnCount"], "GOOGLE_RESPONSE_GRID_BOUNDS_INVALID"
        )
        if (
            row_count != expected_sheet.row_count
            or column_count != expected_sheet.column_count
        ):
            _fail("GOOGLE_RESPONSE_GRID_BOUNDS_MISMATCH")

        cells = _map_grid_blocks(
            sheet_id=sheet_id,
            raw_blocks=sheet["data"],
            configured_ranges=configured_ranges,
            seen_range_ids=seen_range_ids,
        )
        merges = _map_merges(sheet["merges"], expected_sheet)
        try:
            mapped_sheets.append(
                SheetSnapshot(
                    sheet_id=sheet_id,
                    title=title,
                    hidden=hidden,
                    row_count=row_count,
                    column_count=column_count,
                    cells=tuple(
                        sorted(cells, key=lambda item: (item.row_index, item.column_index))
                    ),
                    merges=tuple(
                        sorted(
                            merges,
                            key=lambda item: (
                                item.start_row_index,
                                item.end_row_index,
                                item.start_column_index,
                                item.end_column_index,
                            ),
                        )
                    ),
                )
            )
        except ValidationError:
            _fail("GOOGLE_RESPONSE_MERGE_INVALID")

    expected_sheet_ids = set(configured_sheets)
    if seen_sheet_ids != expected_sheet_ids:
        _fail("GOOGLE_RESPONSE_SHEET_MISSING")
    if len(seen_range_ids) != len(configuration.ranges):
        _fail("GOOGLE_RESPONSE_RANGE_MISSING")

    try:
        snapshot = SpreadsheetSnapshot(
            spreadsheet_id=spreadsheet_id,
            sheets=tuple(sorted(mapped_sheets, key=lambda item: item.sheet_id)),
        )
    except ValidationError:
        _fail("GOOGLE_RESPONSE_SNAPSHOT_INVALID")

    covered_ranges = tuple(
        CoveredRange(
            range_id=value.range_id,
            sheet_id=value.sheet_id,
            start_row_index=value.start_row_index,
            end_row_index=value.end_row_index,
            start_column_index=value.start_column_index,
            end_column_index=value.end_column_index,
        )
        for value in sorted(configuration.ranges, key=lambda item: item.range_id)
    )
    proof = ConfiguredRangeCoverageProof(
        configuration_identity=configuration.configuration_identity,
        config_version=configuration.config_version,
        mapper_version=configuration.mapper_version,
        method=configuration.method,
        include_grid_data=configuration.include_grid_data,
        expected_range_count=len(configuration.ranges),
        observed_range_count=len(seen_range_ids),
        covered_ranges=covered_ranges,
    )
    return _create_configured_read_result(
        snapshot,
        proof,
        configuration.configuration_identity,
    )


def _map_grid_blocks(
    *,
    sheet_id: int,
    raw_blocks: object,
    configured_ranges: Dict[Tuple[int, int, int], ConfiguredRange],
    seen_range_ids: Set[str],
) -> List[CellData]:
    blocks = _array(raw_blocks, "GOOGLE_RESPONSE_SHEET_DATA_INVALID")
    cells: List[CellData] = []
    for raw_block in blocks:
        block = _mapping(raw_block, "GOOGLE_RESPONSE_GRID_BLOCK_INVALID")
        _keys(
            block,
            required=set(),
            allowed={"startRow", "startColumn", "rowData"},
            code="GOOGLE_RESPONSE_GRID_BLOCK_INVALID",
        )
        start_row = _optional_non_negative_int(block, "startRow", default=0)
        start_column = _optional_non_negative_int(block, "startColumn", default=0)
        configured_range = configured_ranges.get((sheet_id, start_row, start_column))
        if configured_range is None:
            _fail("GOOGLE_RESPONSE_RANGE_UNEXPECTED")
        if configured_range.range_id in seen_range_ids:
            _fail("GOOGLE_RESPONSE_RANGE_DUPLICATE")
        seen_range_ids.add(configured_range.range_id)

        raw_rows = _array(
            block.get("rowData", []), "GOOGLE_RESPONSE_ROW_DATA_INVALID"
        )
        height = configured_range.end_row_index - configured_range.start_row_index
        width = configured_range.end_column_index - configured_range.start_column_index
        if len(raw_rows) > height:
            _fail("GOOGLE_RESPONSE_RANGE_BOUNDS_EXCEEDED")
        for row_offset, raw_row in enumerate(raw_rows):
            row = _mapping(raw_row, "GOOGLE_RESPONSE_ROW_INVALID")
            _keys(
                row,
                required=set(),
                allowed={"values"},
                code="GOOGLE_RESPONSE_ROW_INVALID",
            )
            raw_values = _array(
                row.get("values", []), "GOOGLE_RESPONSE_ROW_VALUES_INVALID"
            )
            if len(raw_values) > width:
                _fail("GOOGLE_RESPONSE_RANGE_BOUNDS_EXCEEDED")
            for column_offset, raw_cell in enumerate(raw_values):
                cell = _map_cell(
                    raw_cell,
                    row_index=start_row + row_offset,
                    column_index=start_column + column_offset,
                )
                if cell is not None:
                    cells.append(cell)
    return cells


def _map_cell(
    raw_cell: object,
    *,
    row_index: int,
    column_index: int,
) -> Optional[CellData]:
    cell = _mapping(raw_cell, "GOOGLE_RESPONSE_CELL_INVALID")
    _keys(
        cell,
        required=set(),
        allowed={
            "formattedValue",
            "effectiveValue",
            "userEnteredValue",
            "hyperlink",
            "textFormatRuns",
            "dataValidation",
        },
        code="GOOGLE_RESPONSE_CELL_SHAPE_UNSUPPORTED",
    )
    formatted_value = _optional_string(cell, "formattedValue")
    effective_value = _optional_google_value(cell, "effectiveValue")
    user_entered_value = _optional_google_value(cell, "userEnteredValue")
    hyperlink = _optional_string(cell, "hyperlink")
    text_format_runs = _map_text_format_runs(cell.get("textFormatRuns", []))
    data_validation = _map_optional_data_validation(cell)

    if (
        formatted_value is None
        and effective_value is None
        and user_entered_value is None
        and hyperlink is None
        and not text_format_runs
        and data_validation is None
    ):
        return None
    try:
        return CellData(
            row_index=row_index,
            column_index=column_index,
            formatted_value=formatted_value,
            effective_value=effective_value,
            user_entered_value=user_entered_value,
            hyperlink=hyperlink,
            text_format_runs=text_format_runs,
            data_validation=data_validation,
        )
    except ValidationError:
        _fail("GOOGLE_RESPONSE_CELL_INVALID")


def _optional_google_value(
    cell: Mapping[str, object], key: str
) -> Optional[GoogleValue]:
    if key not in cell:
        return None
    value = _mapping(cell[key], "GOOGLE_RESPONSE_VALUE_INVALID")
    allowed = {
        "stringValue",
        "numberValue",
        "boolValue",
        "formulaValue",
        "errorValue",
    }
    if not value or set(value) - allowed:
        _fail("GOOGLE_RESPONSE_VALUE_UNSUPPORTED")
    if len(value) != 1:
        _fail("GOOGLE_RESPONSE_VALUE_UNSUPPORTED")
    branch, branch_value = next(iter(value.items()))
    try:
        if branch == "stringValue":
            return GoogleValue(
                string_value=_strict_string(branch_value, "GOOGLE_RESPONSE_VALUE_INVALID")
            )
        if branch == "numberValue":
            if type(branch_value) not in {int, float}:
                _fail("GOOGLE_RESPONSE_VALUE_INVALID")
            if isinstance(branch_value, float) and not math.isfinite(branch_value):
                _fail("GOOGLE_RESPONSE_VALUE_INVALID")
            return GoogleValue(number_value=branch_value)
        if branch == "boolValue":
            if type(branch_value) is not bool:
                _fail("GOOGLE_RESPONSE_VALUE_INVALID")
            return GoogleValue(bool_value=branch_value)
        if branch == "formulaValue":
            return GoogleValue(
                formula_value=_strict_string(
                    branch_value, "GOOGLE_RESPONSE_VALUE_INVALID"
                )
            )
        if branch == "errorValue":
            error = _mapping(branch_value, "GOOGLE_RESPONSE_VALUE_INVALID")
            _keys(
                error,
                required={"type"},
                allowed={"type", "message"},
                code="GOOGLE_RESPONSE_VALUE_INVALID",
            )
            message = _optional_string(error, "message")
            return GoogleValue(
                error_value=GoogleError(
                    error_type=_strict_string(
                        error["type"], "GOOGLE_RESPONSE_VALUE_INVALID"
                    ),
                    message=message,
                )
            )
    except ValidationError:
        _fail("GOOGLE_RESPONSE_VALUE_INVALID")
    _fail("GOOGLE_RESPONSE_VALUE_UNSUPPORTED")


def _map_text_format_runs(raw_runs: object) -> Tuple[TextFormatRun, ...]:
    runs = _array(raw_runs, "GOOGLE_RESPONSE_TEXT_RUNS_INVALID")
    mapped: List[TextFormatRun] = []
    for raw_run in runs:
        run = _mapping(raw_run, "GOOGLE_RESPONSE_TEXT_RUN_INVALID")
        _keys(
            run,
            required=set(),
            allowed={"startIndex", "format"},
            code="GOOGLE_RESPONSE_TEXT_RUN_INVALID",
        )
        start_index = _optional_non_negative_int(run, "startIndex", default=0)
        raw_format = _mapping(run.get("format", {}), "GOOGLE_RESPONSE_TEXT_FORMAT_INVALID")
        _keys(
            raw_format,
            required=set(),
            allowed={"link"},
            code="GOOGLE_RESPONSE_TEXT_FORMAT_INVALID",
        )
        link = None
        if "link" in raw_format:
            raw_link = _mapping(
                raw_format["link"], "GOOGLE_RESPONSE_TEXT_LINK_INVALID"
            )
            _keys(
                raw_link,
                required={"uri"},
                allowed={"uri"},
                code="GOOGLE_RESPONSE_TEXT_LINK_INVALID",
            )
            try:
                link = TextFormatLink(
                    uri=_strict_string(
                        raw_link["uri"], "GOOGLE_RESPONSE_TEXT_LINK_INVALID"
                    )
                )
            except ValidationError:
                _fail("GOOGLE_RESPONSE_TEXT_LINK_INVALID")
        try:
            mapped.append(TextFormatRun(start_index=start_index, link=link))
        except ValidationError:
            _fail("GOOGLE_RESPONSE_TEXT_RUN_INVALID")
    try:
        CellData(row_index=0, column_index=0, text_format_runs=tuple(mapped))
    except ValidationError:
        _fail("GOOGLE_RESPONSE_TEXT_RUN_INVALID")
    return tuple(mapped)


def _map_optional_data_validation(
    cell: Mapping[str, object],
) -> Optional[DataValidation]:
    if "dataValidation" not in cell:
        return None
    validation = _mapping(
        cell["dataValidation"], "GOOGLE_RESPONSE_VALIDATION_INVALID"
    )
    _keys(
        validation,
        required={"condition"},
        allowed={"condition", "inputMessage", "strict", "showCustomUi"},
        code="GOOGLE_RESPONSE_VALIDATION_INVALID",
    )
    condition = _mapping(
        validation["condition"], "GOOGLE_RESPONSE_VALIDATION_INVALID"
    )
    _keys(
        condition,
        required={"type"},
        allowed={"type", "values"},
        code="GOOGLE_RESPONSE_VALIDATION_INVALID",
    )
    condition_values = []
    for raw_value in _array(
        condition.get("values", []), "GOOGLE_RESPONSE_VALIDATION_INVALID"
    ):
        value = _mapping(raw_value, "GOOGLE_RESPONSE_VALIDATION_INVALID")
        _keys(
            value,
            required=set(),
            allowed={"relativeDate", "userEnteredValue"},
            code="GOOGLE_RESPONSE_VALIDATION_INVALID",
        )
        if len(value) != 1:
            _fail("GOOGLE_RESPONSE_VALIDATION_INVALID")
        try:
            if "relativeDate" in value:
                condition_values.append(
                    ConditionValue(
                        relative_date=_strict_string(
                            value["relativeDate"],
                            "GOOGLE_RESPONSE_VALIDATION_INVALID",
                        )
                    )
                )
            else:
                condition_values.append(
                    ConditionValue(
                        user_entered_value=_strict_string(
                            value["userEnteredValue"],
                            "GOOGLE_RESPONSE_VALIDATION_INVALID",
                        )
                    )
                )
        except ValidationError:
            _fail("GOOGLE_RESPONSE_VALIDATION_INVALID")
    try:
        return DataValidation(
            condition=DataValidationCondition(
                condition_type=_strict_string(
                    condition["type"], "GOOGLE_RESPONSE_VALIDATION_INVALID"
                ),
                values=tuple(condition_values),
            ),
            input_message=_optional_string(validation, "inputMessage"),
            strict=_optional_bool(validation, "strict", default=None),
            show_custom_ui=_optional_bool(validation, "showCustomUi", default=None),
        )
    except ValidationError:
        _fail("GOOGLE_RESPONSE_VALIDATION_INVALID")


def _map_merges(raw_merges: object, sheet: ConfiguredSheet) -> List[GridRange]:
    merges = _array(raw_merges, "GOOGLE_RESPONSE_MERGES_INVALID")
    mapped = []
    required = {"endRowIndex", "endColumnIndex"}
    allowed = required | {"sheetId", "startRowIndex", "startColumnIndex"}
    for raw_merge in merges:
        merge = _mapping(raw_merge, "GOOGLE_RESPONSE_MERGE_INVALID")
        _keys(
            merge,
            required=required,
            allowed=allowed,
            code="GOOGLE_RESPONSE_MERGE_INVALID",
        )
        try:
            mapped_range = GridRange(
                sheet_id=_optional_non_negative_int(
                    merge, "sheetId", default=sheet.sheet_id
                ),
                start_row_index=_optional_non_negative_int(
                    merge, "startRowIndex", default=0
                ),
                end_row_index=_non_negative_int(
                    merge["endRowIndex"], "GOOGLE_RESPONSE_MERGE_INVALID"
                ),
                start_column_index=_optional_non_negative_int(
                    merge, "startColumnIndex", default=0
                ),
                end_column_index=_non_negative_int(
                    merge["endColumnIndex"], "GOOGLE_RESPONSE_MERGE_INVALID"
                ),
            )
        except ValidationError:
            _fail("GOOGLE_RESPONSE_MERGE_INVALID")
        if mapped_range.sheet_id != sheet.sheet_id:
            _fail("GOOGLE_RESPONSE_MERGE_INVALID")
        if (
            mapped_range.end_row_index > sheet.row_count
            or mapped_range.end_column_index > sheet.column_count
        ):
            _fail("GOOGLE_RESPONSE_MERGE_INVALID")
        mapped.append(mapped_range)
    return mapped


def _keys(
    value: Mapping[str, object],
    *,
    required: Set[str],
    allowed: Set[str],
    code: str,
    missing_codes: Optional[Mapping[str, str]] = None,
) -> None:
    if any(not isinstance(key, str) for key in value):
        _fail(code)
    extra = set(value) - allowed
    if extra:
        _fail(code)
    missing = required - set(value)
    if missing:
        if missing_codes:
            for key, missing_code in missing_codes.items():
                if key in missing:
                    _fail(missing_code)
        _fail(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _array(value: object, code: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        _fail(code)
    return value


def _strict_string(value: object, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    return value


def _optional_string(value: Mapping[str, object], key: str) -> Optional[str]:
    if key not in value:
        return None
    return _strict_string(value[key], "GOOGLE_RESPONSE_FIELD_TYPE_INVALID")


def _optional_bool(
    value: Mapping[str, object], key: str, *, default: Optional[bool]
) -> Optional[bool]:
    if key not in value:
        return default
    if type(value[key]) is not bool:
        _fail("GOOGLE_RESPONSE_FIELD_TYPE_INVALID")
    return value[key]


def _non_negative_int(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        _fail(code)
    return value


def _optional_non_negative_int(
    value: Mapping[str, object], key: str, *, default: int
) -> int:
    if key not in value:
        return default
    return _non_negative_int(value[key], "GOOGLE_RESPONSE_OFFSET_INVALID")


def _fail(code: str) -> None:
    raise GoogleSheetsResponseError(code) from None


__all__ = ["GoogleSheetsResponseError", "map_google_sheets_response"]
