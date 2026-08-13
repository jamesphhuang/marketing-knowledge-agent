from __future__ import annotations

from copy import deepcopy
import inspect

import pytest

from marketing_knowledge_agent.canonical_serialization import compute_source_fingerprint
from marketing_knowledge_agent.google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredReadPlan,
    ConfiguredReadResult,
    ConfiguredSheet,
    REQUIRED_GOOGLE_RESPONSE_FIELDS,
)
from marketing_knowledge_agent.google_sheets_response_mapper import (
    GoogleSheetsResponseError,
    map_google_sheets_response,
)


RAW_SENTINEL = "RAW_CELL_BODY_MUST_NOT_APPEAR"


def _plan() -> ConfiguredReadPlan:
    return ConfiguredReadPlan(
        spreadsheet_id="synthetic-spreadsheet-wp0",
        config_version="synthetic-config-v1",
        sheets=(
            ConfiguredSheet(
                sheet_id=7,
                title="Synthetic Inputs",
                hidden=False,
                row_count=20,
                column_count=8,
            ),
            ConfiguredSheet(
                sheet_id=9,
                title="Synthetic Hidden Map",
                hidden=True,
                row_count=10,
                column_count=4,
            ),
        ),
        ranges=(
            ConfiguredRange(
                range_id="inputs",
                sheet_id=7,
                start_row_index=2,
                end_row_index=5,
                start_column_index=1,
                end_column_index=5,
            ),
            ConfiguredRange(
                range_id="hidden-map",
                sheet_id=9,
                start_row_index=0,
                end_row_index=2,
                start_column_index=0,
                end_column_index=2,
            ),
        ),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
    )


def _response() -> dict:
    return {
        "spreadsheetId": "synthetic-spreadsheet-wp0",
        "sheets": [
            {
                "properties": {
                    "sheetId": 7,
                    "title": "Synthetic Inputs",
                    "gridProperties": {"rowCount": 20, "columnCount": 8},
                },
                "data": [
                    {
                        "startRow": 2,
                        "startColumn": 1,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "formattedValue": RAW_SENTINEL,
                                        "effectiveValue": {"stringValue": RAW_SENTINEL},
                                        "userEnteredValue": {"stringValue": RAW_SENTINEL},
                                    },
                                    {
                                        "formattedValue": "0",
                                        "effectiveValue": {"numberValue": 0},
                                    },
                                    {
                                        "formattedValue": "FALSE",
                                        "effectiveValue": {"boolValue": False},
                                    },
                                    {
                                        "formattedValue": "2",
                                        "effectiveValue": {"numberValue": 2},
                                        "userEnteredValue": {"formulaValue": "=1+1"},
                                    },
                                ]
                            },
                            {
                                "values": [
                                    {
                                        "formattedValue": "whole link",
                                        "hyperlink": "https://example.com/synthetic-whole",
                                    },
                                    {
                                        "formattedValue": "rich link text",
                                        "textFormatRuns": [
                                            {
                                                "startIndex": 0,
                                                "format": {
                                                    "link": {
                                                        "uri": "https://example.org/synthetic-rich"
                                                    }
                                                },
                                            },
                                            {"startIndex": 5, "format": {}},
                                        ],
                                    },
                                    {
                                        "dataValidation": {
                                            "condition": {
                                                "type": "BOOLEAN",
                                                "values": [
                                                    {"userEnteredValue": "TRUE"},
                                                    {"userEnteredValue": "FALSE"},
                                                ],
                                            },
                                            "inputMessage": "synthetic checkbox",
                                            "strict": True,
                                            "showCustomUi": False,
                                        }
                                    },
                                    {
                                        "formattedValue": "#DIV/0!",
                                        "effectiveValue": {
                                            "errorValue": {
                                                "type": "DIVIDE_BY_ZERO",
                                                "message": "synthetic error",
                                            }
                                        },
                                    },
                                ]
                            },
                            {
                                "values": [
                                    {},
                                    {
                                        "formattedValue": "",
                                        "effectiveValue": {"stringValue": ""},
                                        "userEnteredValue": {"stringValue": ""},
                                    },
                                ]
                            },
                        ],
                    }
                ],
                "merges": [
                    {
                        "sheetId": 7,
                        "startRowIndex": 3,
                        "endRowIndex": 4,
                        "startColumnIndex": 1,
                        "endColumnIndex": 3,
                    }
                ],
            },
            {
                "properties": {
                    "sheetId": 9,
                    "title": "Synthetic Hidden Map",
                    "hidden": True,
                    "gridProperties": {"rowCount": 10, "columnCount": 4},
                },
                "data": [{"rowData": [{"values": [{"formattedValue": "map"}]}]}],
                "merges": [],
            },
        ],
    }


def test_maps_required_google_response_semantics_and_absolute_coordinates():
    result = map_google_sheets_response(_response(), _plan())
    first, hidden = result.snapshot.sheets

    assert isinstance(result, ConfiguredReadResult)
    assert result.configuration_identity.startswith("sha256:")
    assert result.coverage_proof.expected_range_count == 2
    assert result.coverage_proof.observed_range_count == 2
    assert (first.sheet_id, first.title, first.hidden) == (7, "Synthetic Inputs", False)
    assert (hidden.sheet_id, hidden.hidden) == (9, True)

    by_coordinate = {(cell.row_index, cell.column_index): cell for cell in first.cells}
    assert by_coordinate[(2, 1)].effective_value.string_value == RAW_SENTINEL
    assert by_coordinate[(2, 2)].effective_value.number_value == 0
    assert by_coordinate[(2, 3)].effective_value.bool_value is False
    assert by_coordinate[(2, 4)].user_entered_value.formula_value == "=1+1"
    assert by_coordinate[(3, 1)].hyperlink.endswith("synthetic-whole")
    assert [run.start_index for run in by_coordinate[(3, 2)].text_format_runs] == [0, 5]
    assert by_coordinate[(3, 2)].text_format_runs[0].link.uri.endswith("synthetic-rich")
    assert by_coordinate[(3, 2)].text_format_runs[1].link is None
    assert by_coordinate[(3, 3)].data_validation.condition.condition_type == "BOOLEAN"
    assert by_coordinate[(3, 3)].data_validation.show_custom_ui is False
    assert by_coordinate[(3, 4)].effective_value.error_value.error_type == "DIVIDE_BY_ZERO"
    assert (4, 1) not in by_coordinate
    assert by_coordinate[(4, 2)].formatted_value == ""
    assert by_coordinate[(4, 2)].effective_value.string_value == ""
    assert by_coordinate[(4, 2)].user_entered_value.string_value == ""
    assert hidden.cells[0].row_index == 0
    assert hidden.cells[0].column_index == 0
    assert first.merges[0].start_row_index == 3


def test_mapping_is_deterministic_and_canonicalizes_incidental_sheet_order():
    response = _response()
    reordered = deepcopy(response)
    reordered["sheets"].reverse()

    first = map_google_sheets_response(response, _plan())
    second = map_google_sheets_response(deepcopy(response), _plan())
    reordered_result = map_google_sheets_response(reordered, _plan())

    assert first.snapshot == second.snapshot == reordered_result.snapshot
    assert first.coverage_proof == second.coverage_proof == reordered_result.coverage_proof
    assert compute_source_fingerprint(first.snapshot) == compute_source_fingerprint(
        reordered_result.snapshot
    )


def test_multi_grid_block_order_is_incidental():
    response = _response()
    extra_range = ConfiguredRange(
        range_id="inputs-second-block",
        sheet_id=7,
        start_row_index=8,
        end_row_index=10,
        start_column_index=2,
        end_column_index=4,
    )
    plan = ConfiguredReadPlan(
        spreadsheet_id=_plan().spreadsheet_id,
        config_version=_plan().config_version,
        sheets=_plan().sheets,
        ranges=_plan().ranges + (extra_range,),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
    )
    response["sheets"][0]["data"].append(
        {
            "startRow": 8,
            "startColumn": 2,
            "rowData": [{"values": [{"formattedValue": "second block"}]}],
        }
    )
    reordered = deepcopy(response)
    reordered["sheets"][0]["data"].reverse()

    assert map_google_sheets_response(response, plan).snapshot == (
        map_google_sheets_response(reordered, plan).snapshot
    )


def test_sparse_omitted_interior_and_trailing_empty_encodings_normalize_equally():
    response = _response()
    sparse = deepcopy(response)
    sparse["sheets"][1]["data"][0] = {"startRow": 0, "startColumn": 0}

    explicit = deepcopy(response)
    explicit["sheets"][1]["data"][0] = {
        "rowData": [{"values": [{}, {}]}, {"values": [{}, {}]}]
    }

    sparse_snapshot = map_google_sheets_response(sparse, _plan()).snapshot
    explicit_snapshot = map_google_sheets_response(explicit, _plan()).snapshot

    assert sparse_snapshot == explicit_snapshot
    assert compute_source_fingerprint(sparse_snapshot) == compute_source_fingerprint(
        explicit_snapshot
    )


def test_result_repr_errors_and_logs_do_not_reflect_raw_payload(caplog):
    result = map_google_sheets_response(_response(), _plan())
    assert RAW_SENTINEL not in repr(result)
    assert RAW_SENTINEL not in repr(result.snapshot)
    assert "SpreadsheetSnapshot" not in repr(result)

    malformed = _response()
    malformed["sheets"][0]["data"][0]["rowData"][0]["values"][0][
        "effectiveValue"
    ] = {"unsupportedValue": RAW_SENTINEL}

    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(malformed, _plan())

    assert RAW_SENTINEL not in str(caught.value)
    assert RAW_SENTINEL not in repr(caught.value)
    assert caught.value.code == "GOOGLE_RESPONSE_VALUE_UNSUPPORTED"
    assert caught.value.__context__ is None

    invalid_dto = _response()
    invalid_dto["sheets"][0]["data"][0]["rowData"][1]["values"][1][
        "textFormatRuns"
    ][0]["format"]["link"]["uri"] = ""
    with pytest.raises(GoogleSheetsResponseError) as caught_dto_error:
        map_google_sheets_response(invalid_dto, _plan())

    assert caught_dto_error.value.code == "GOOGLE_RESPONSE_TEXT_LINK_INVALID"
    assert caught_dto_error.value.__context__ is None
    assert caplog.records == []


def test_omitted_zero_merge_offsets_normalize_to_explicit_zero():
    response = _response()
    response["sheets"][1]["merges"] = [
        {
            "sheetId": 9,
            "endRowIndex": 1,
            "endColumnIndex": 2,
        }
    ]
    explicit = deepcopy(response)
    explicit["sheets"][1]["merges"][0].update(
        {"startRowIndex": 0, "startColumnIndex": 0}
    )

    omitted_result = map_google_sheets_response(response, _plan())
    explicit_result = map_google_sheets_response(explicit, _plan())

    assert omitted_result.snapshot == explicit_result.snapshot
    assert compute_source_fingerprint(omitted_result.snapshot) == (
        compute_source_fingerprint(explicit_result.snapshot)
    )


def test_omitted_merges_normalizes_to_explicit_empty_merges():
    omitted = _response()
    omitted["sheets"][1].pop("merges")
    explicit = _response()

    omitted_result = map_google_sheets_response(omitted, _plan())
    explicit_result = map_google_sheets_response(explicit, _plan())

    assert omitted_result.snapshot == explicit_result.snapshot
    assert compute_source_fingerprint(omitted_result.snapshot) == (
        compute_source_fingerprint(explicit_result.snapshot)
    )


def test_omitted_validation_booleans_normalize_to_explicit_false():
    omitted = _response()
    omitted_validation = omitted["sheets"][0]["data"][0]["rowData"][1]["values"][2][
        "dataValidation"
    ]
    omitted_validation.pop("strict")
    omitted_validation.pop("showCustomUi")

    explicit = _response()
    explicit_validation = explicit["sheets"][0]["data"][0]["rowData"][1]["values"][2][
        "dataValidation"
    ]
    explicit_validation["strict"] = False
    explicit_validation["showCustomUi"] = False

    omitted_result = map_google_sheets_response(omitted, _plan())
    explicit_result = map_google_sheets_response(explicit, _plan())

    assert omitted_result.snapshot == explicit_result.snapshot
    assert compute_source_fingerprint(omitted_result.snapshot) == (
        compute_source_fingerprint(explicit_result.snapshot)
    )


def test_explicit_true_validation_booleans_remain_true():
    response = _response()
    validation = response["sheets"][0]["data"][0]["rowData"][1]["values"][2][
        "dataValidation"
    ]
    validation["strict"] = True
    validation["showCustomUi"] = True

    result = map_google_sheets_response(response, _plan())
    mapped_validation = next(
        cell.data_validation
        for cell in result.snapshot.sheets[0].cells
        if (cell.row_index, cell.column_index) == (3, 3)
    )

    assert mapped_validation.strict is True
    assert mapped_validation.show_custom_ui is True


def test_omitted_zero_sheet_id_normalizes_to_explicit_zero():
    plan = ConfiguredReadPlan(
        spreadsheet_id="synthetic-zero-sheet",
        config_version="synthetic-zero-sheet-v1",
        sheets=(
            ConfiguredSheet(
                sheet_id=0,
                title="Synthetic Zero Sheet",
                hidden=False,
                row_count=2,
                column_count=2,
            ),
        ),
        ranges=(
            ConfiguredRange(
                range_id="zero-sheet-range",
                sheet_id=0,
                start_row_index=0,
                end_row_index=2,
                start_column_index=0,
                end_column_index=2,
            ),
        ),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
    )
    omitted = {
        "spreadsheetId": "synthetic-zero-sheet",
        "sheets": [
            {
                "properties": {
                    "title": "Synthetic Zero Sheet",
                    "gridProperties": {"rowCount": 2, "columnCount": 2},
                },
                "data": [{"rowData": [{"values": [{"formattedValue": "zero"}]}]}],
                "merges": [],
            }
        ],
    }
    explicit = deepcopy(omitted)
    explicit["sheets"][0]["properties"]["sheetId"] = 0

    omitted_result = map_google_sheets_response(omitted, plan)
    explicit_result = map_google_sheets_response(explicit, plan)

    assert omitted_result.snapshot.sheets[0].sheet_id == 0
    assert omitted_result.snapshot == explicit_result.snapshot
    assert compute_source_fingerprint(omitted_result.snapshot) == (
        compute_source_fingerprint(explicit_result.snapshot)
    )


def test_omitted_sheet_id_does_not_infer_configured_non_zero_id():
    response = _response()
    response["sheets"][0]["properties"].pop("sheetId")

    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(response, _plan())

    assert caught.value.code == "GOOGLE_RESPONSE_SHEET_UNEXPECTED"


def test_explicit_correct_non_zero_sheet_id_still_maps():
    result = map_google_sheets_response(_response(), _plan())

    assert [sheet.sheet_id for sheet in result.snapshot.sheets] == [7, 9]


def test_mapper_has_no_client_credential_or_persistence_interface():
    parameters = set(inspect.signature(map_google_sheets_response).parameters)

    assert parameters == {"response", "configuration"}
    assert not parameters & {
        "client",
        "credential",
        "credentials",
        "token",
        "auth_header",
        "output_path",
    }
