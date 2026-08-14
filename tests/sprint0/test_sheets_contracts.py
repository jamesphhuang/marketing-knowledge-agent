from __future__ import annotations

from typing import get_type_hints

import pytest
from pydantic import ValidationError

from marketing_knowledge_agent.sheets_contracts import (
    CellData,
    ConditionValue,
    DataValidation,
    DataValidationCondition,
    GoogleError,
    GoogleValue,
    SheetSnapshot,
    SheetsReadRequest,
    SheetsReader,
    SpreadsheetSnapshot,
    TextFormatLink,
    TextFormatRun,
)
from sprint0_fixtures import load_synthetic_json


def _snapshot() -> SpreadsheetSnapshot:
    return SpreadsheetSnapshot(**load_synthetic_json("cell_data_wp1.json"))


def test_snapshot_preserves_distinct_google_cell_value_representations():
    snapshot = _snapshot()
    sheet = snapshot.sheets[0]

    plain_text = sheet.cells[0]
    number = sheet.cells[1]
    checkbox = sheet.cells[2]
    formula = sheet.cells[3]

    assert plain_text.formatted_value == "Synthetic Plain Text"
    assert plain_text.effective_value.string_value == "Synthetic Plain Text"
    assert plain_text.user_entered_value.string_value == "Synthetic Plain Text"
    assert number.effective_value.number_value == 42
    assert checkbox.effective_value.bool_value is True
    assert formula.user_entered_value.formula_value == "=SUM(B1,8)"
    assert formula.effective_value.number_value == 50
    assert formula.formatted_value == "50"


def test_hyperlink_and_text_format_run_links_remain_separate_source_shapes():
    sheet = _snapshot().sheets[0]
    whole_cell_link = sheet.cells[4]
    rich_text = sheet.cells[5]

    assert whole_cell_link.hyperlink == "https://example.com/synthetic-whole-cell"
    assert whole_cell_link.text_format_runs == ()
    assert rich_text.hyperlink is None
    assert [run.start_index for run in rich_text.text_format_runs] == [0, 14, 23]
    assert rich_text.text_format_runs[0].link.uri == "https://example.org/synthetic-rich-one"
    assert rich_text.text_format_runs[1].link is None
    assert rich_text.text_format_runs[2].link.uri == "https://example.net/synthetic-rich-two"


def test_checkbox_validation_metadata_is_transport_only():
    validation = _snapshot().sheets[0].cells[2].data_validation

    assert validation.condition.condition_type == "BOOLEAN"
    assert [value.user_entered_value for value in validation.condition.values] == [
        "TRUE",
        "FALSE",
    ]
    assert validation.input_message == "Synthetic checkbox"
    assert validation.strict is True
    assert validation.show_custom_ui is True


def test_blank_cell_merge_metadata_and_source_coordinates_are_preserved():
    snapshot = _snapshot()
    sheet = snapshot.sheets[0]
    blank = sheet.cells[6]
    merge = sheet.merges[0]

    assert snapshot.spreadsheet_id == "synthetic-spreadsheet-wp1"
    assert (sheet.sheet_id, sheet.title) == (101, "Synthetic Inputs")
    assert (blank.row_index, blank.column_index) == (2, 0)
    assert blank.formatted_value is None
    assert blank.effective_value is None
    assert blank.user_entered_value is None
    assert (
        merge.sheet_id,
        merge.start_row_index,
        merge.end_row_index,
        merge.start_column_index,
        merge.end_column_index,
    ) == (101, 3, 5, 0, 1)


def test_multiple_sheets_retain_properties_including_hidden_state():
    first, second = _snapshot().sheets

    assert (first.sheet_id, first.title, first.hidden) == (101, "Synthetic Inputs", False)
    assert (second.sheet_id, second.title, second.hidden) == (
        202,
        "Synthetic Hidden Inputs",
        True,
    )
    assert second.cells[0].formatted_value == "虛構資料 α"


def test_snapshot_contracts_are_read_only():
    snapshot = _snapshot()

    with pytest.raises((TypeError, ValidationError)):
        snapshot.spreadsheet_id = "synthetic-mutated-id"


def test_snapshot_repr_does_not_expose_cell_payload():
    snapshot = _snapshot()

    rendered = repr(snapshot)

    assert "Synthetic Plain Text" not in rendered
    assert "SpreadsheetSnapshot" in rendered
    assert "sheet_count=2" in rendered


def test_all_payload_bearing_google_dto_repr_and_str_surfaces_are_redacted():
    sentinel = "SYNTHETIC_NESTED_DTO_PAYLOAD_SENTINEL"
    error = GoogleError(error_type="SYNTHETIC_ERROR", message=sentinel)
    effective = GoogleValue(error_value=error)
    entered = GoogleValue(formula_value=f"={sentinel}")
    link = TextFormatLink(uri=f"https://example.test/{sentinel}")
    run = TextFormatRun(start_index=0, link=link)
    condition_value = ConditionValue(user_entered_value=sentinel)
    condition = DataValidationCondition(
        condition_type="TEXT_EQ",
        values=(condition_value,),
    )
    validation = DataValidation(condition=condition, input_message=sentinel)
    cell = CellData(
        row_index=0,
        column_index=0,
        formatted_value=sentinel,
        effective_value=effective,
        user_entered_value=entered,
        hyperlink=f"https://example.test/{sentinel}",
        text_format_runs=(run,),
        data_validation=validation,
    )
    sheet = SheetSnapshot(
        sheet_id=101,
        title=sentinel,
        row_count=1,
        column_count=1,
        cells=(cell,),
    )
    snapshot = SpreadsheetSnapshot(spreadsheet_id=sentinel, sheets=(sheet,))

    payload_bearing_values = (
        error,
        effective,
        entered,
        link,
        run,
        condition_value,
        condition,
        validation,
        cell,
        sheet,
        snapshot,
    )
    for value in payload_bearing_values:
        assert sentinel not in repr(value)
        assert sentinel not in str(value)

    assert repr(cell) == "CellData(row_index=0, column_index=0)"
    assert repr(sheet) == "SheetSnapshot(sheet_id=101, row_count=1, column_count=1)"
    assert repr(snapshot) == "SpreadsheetSnapshot(sheet_count=1)"


def test_payload_safe_repr_does_not_change_trusted_internal_serialization():
    sentinel = "SYNTHETIC_TRUSTED_SERIALIZATION_SENTINEL"
    cell = CellData(
        row_index=0,
        column_index=0,
        formatted_value=sentinel,
        user_entered_value=GoogleValue(formula_value=f"={sentinel}"),
    )

    dumped = cell.model_dump() if hasattr(cell, "model_dump") else cell.dict()

    assert dumped["formatted_value"] == sentinel
    assert dumped["user_entered_value"]["formula_value"] == f"={sentinel}"
    assert sentinel not in repr(cell)


def test_google_value_rejects_ambiguous_or_unknown_value_branches():
    with pytest.raises(ValidationError, match="GOOGLE_VALUE_MULTIPLE_KINDS"):
        GoogleValue(string_value="synthetic", bool_value=True)

    with pytest.raises(ValidationError):
        GoogleValue(unsupported_value="synthetic")


def test_sheet_rejects_out_of_bounds_cells_and_overlapping_merges():
    payload = load_synthetic_json("cell_data_wp1.json")
    payload["sheets"][0]["cells"][0]["row_index"] = 8
    with pytest.raises(ValidationError, match="CELL_COORDINATE_OUT_OF_BOUNDS"):
        SpreadsheetSnapshot(**payload)

    payload = load_synthetic_json("cell_data_wp1.json")
    payload["sheets"][0]["merges"].append(
        {
            "sheet_id": 101,
            "start_row_index": 4,
            "end_row_index": 6,
            "start_column_index": 0,
            "end_column_index": 1,
        }
    )
    with pytest.raises(ValidationError, match="MERGE_RANGES_OVERLAP"):
        SpreadsheetSnapshot(**payload)


def test_synthetic_reader_can_be_injected_without_a_write_surface():
    snapshot = _snapshot()
    request = SheetsReadRequest(
        spreadsheet_id=snapshot.spreadsheet_id,
        ranges=("Synthetic Inputs!A1:F8", "Synthetic Hidden Inputs!A1:B2"),
        fields=(
            "formattedValue",
            "effectiveValue",
            "userEnteredValue",
            "hyperlink",
            "textFormatRuns.format.link",
            "dataValidation",
        ),
    )

    class InMemorySheetsReader:
        def __init__(self, value: SpreadsheetSnapshot):
            self.value = value
            self.requests = []

        def read(self, read_request: SheetsReadRequest) -> SpreadsheetSnapshot:
            self.requests.append(read_request)
            return self.value

    reader: SheetsReader = InMemorySheetsReader(snapshot)

    assert isinstance(reader, SheetsReader)
    assert reader.read(request) is snapshot
    assert reader.requests == [request]
    assert set(get_type_hints(SheetsReader.read)) == {"request", "return"}
    assert not hasattr(SheetsReader, "write")
