from __future__ import annotations

from copy import deepcopy

import pytest

from marketing_knowledge_agent.google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredReadContractError,
    ConfiguredReadPlan,
    ConfiguredReadResult,
    ConfiguredSheet,
    REQUIRED_GOOGLE_RESPONSE_FIELDS,
)
from marketing_knowledge_agent.google_sheets_response_mapper import (
    GoogleSheetsResponseError,
    map_google_sheets_response,
)


def _plan() -> ConfiguredReadPlan:
    return ConfiguredReadPlan(
        spreadsheet_id="synthetic-coverage-target",
        config_version="synthetic-coverage-v1",
        sheets=(
            ConfiguredSheet(
                sheet_id=11,
                title="Synthetic Coverage",
                hidden=False,
                row_count=12,
                column_count=6,
            ),
        ),
        ranges=(
            ConfiguredRange(
                range_id="left",
                sheet_id=11,
                start_row_index=1,
                end_row_index=3,
                start_column_index=1,
                end_column_index=3,
            ),
            ConfiguredRange(
                range_id="right",
                sheet_id=11,
                start_row_index=5,
                end_row_index=7,
                start_column_index=3,
                end_column_index=5,
            ),
        ),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
    )


def _response() -> dict:
    return {
        "spreadsheetId": "synthetic-coverage-target",
        "sheets": [
            {
                "properties": {
                    "sheetId": 11,
                    "title": "Synthetic Coverage",
                    "gridProperties": {"rowCount": 12, "columnCount": 6},
                },
                "data": [
                    {
                        "startRow": 1,
                        "startColumn": 1,
                        "rowData": [{"values": [{"formattedValue": "left"}]}],
                    },
                    {
                        "startRow": 5,
                        "startColumn": 3,
                        "rowData": [{"values": [{"formattedValue": "right"}]}],
                    },
                ],
                "merges": [],
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda payload: payload.__setitem__("spreadsheetId", "synthetic-other"),
            "GOOGLE_RESPONSE_TARGET_MISMATCH",
        ),
        (
            lambda payload: payload["sheets"][0]["data"].pop(),
            "GOOGLE_RESPONSE_RANGE_MISSING",
        ),
        (
            lambda payload: payload["sheets"][0]["data"].append(
                deepcopy(payload["sheets"][0]["data"][0])
            ),
            "GOOGLE_RESPONSE_RANGE_DUPLICATE",
        ),
        (
            lambda payload: payload["sheets"][0]["data"].append(
                {"startRow": 9, "startColumn": 0}
            ),
            "GOOGLE_RESPONSE_RANGE_UNEXPECTED",
        ),
        (
            lambda payload: payload["sheets"][0].pop("data"),
            "GOOGLE_RESPONSE_SHEET_DATA_MISSING",
        ),
        (
            lambda payload: payload["sheets"][0]["properties"].__setitem__(
                "title", "Synthetic Renamed"
            ),
            "GOOGLE_RESPONSE_SHEET_MISMATCH",
        ),
        (
            lambda payload: payload["sheets"][0]["properties"][
                "gridProperties"
            ].__setitem__("rowCount", 10),
            "GOOGLE_RESPONSE_GRID_BOUNDS_MISMATCH",
        ),
        (
            lambda payload: payload["sheets"][0]["data"][0]["rowData"][0].__setitem__(
                "values",
                [
                    {"formattedValue": "one"},
                    {"formattedValue": "two"},
                    {"formattedValue": "outside"},
                ],
            ),
            "GOOGLE_RESPONSE_RANGE_BOUNDS_EXCEEDED",
        ),
    ],
)
def test_coverage_failures_are_stable_and_fail_closed(mutate, code):
    payload = _response()
    mutate(payload)

    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(payload, _plan())

    assert caught.value.code == code
    assert str(caught.value) == code


def test_missing_or_unexpected_sheet_fails_closed():
    missing = _response()
    missing["sheets"] = []
    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(missing, _plan())
    assert caught.value.code == "GOOGLE_RESPONSE_SHEET_MISSING"

    extra = _response()
    extra["sheets"].append(deepcopy(extra["sheets"][0]))
    extra["sheets"][1]["properties"]["sheetId"] = 12
    extra["sheets"][1]["properties"]["title"] = "Synthetic Extra"
    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(extra, _plan())
    assert caught.value.code == "GOOGLE_RESPONSE_SHEET_UNEXPECTED"


@pytest.mark.parametrize(
    ("merges", "code"),
    [
        (
            [
                {
                    "sheetId": 11,
                    "startRowIndex": 11,
                    "endRowIndex": 13,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                }
            ],
            "GOOGLE_RESPONSE_MERGE_INVALID",
        ),
        (
            [
                {
                    "sheetId": 11,
                    "startRowIndex": 1,
                    "endRowIndex": 3,
                    "startColumnIndex": 1,
                    "endColumnIndex": 3,
                },
                {
                    "sheetId": 11,
                    "startRowIndex": 2,
                    "endRowIndex": 4,
                    "startColumnIndex": 2,
                    "endColumnIndex": 4,
                },
            ],
            "GOOGLE_RESPONSE_MERGE_INVALID",
        ),
    ],
)
def test_invalid_or_overlapping_merges_fail_closed(merges, code):
    payload = _response()
    payload["sheets"][0]["merges"] = merges

    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(payload, _plan())

    assert caught.value.code == code


def test_plan_rejects_ambiguous_ranges_impossible_bounds_and_unapproved_fields():
    sheet = ConfiguredSheet(
        sheet_id=11,
        title="Synthetic Coverage",
        hidden=False,
        row_count=12,
        column_count=6,
    )
    valid = ConfiguredRange(
        range_id="valid",
        sheet_id=11,
        start_row_index=1,
        end_row_index=4,
        start_column_index=1,
        end_column_index=4,
    )
    overlap = ConfiguredRange(
        range_id="overlap",
        sheet_id=11,
        start_row_index=3,
        end_row_index=5,
        start_column_index=3,
        end_column_index=5,
    )

    with pytest.raises(ConfiguredReadContractError, match="CONFIG_RANGES_OVERLAP"):
        ConfiguredReadPlan(
            spreadsheet_id="synthetic",
            config_version="v1",
            sheets=(sheet,),
            ranges=(valid, overlap),
            fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
        )

    with pytest.raises(ConfiguredReadContractError, match="CONFIG_RANGE_OUT_OF_BOUNDS"):
        ConfiguredReadPlan(
            spreadsheet_id="synthetic",
            config_version="v1",
            sheets=(sheet,),
            ranges=(
                ConfiguredRange(
                    range_id="outside",
                    sheet_id=11,
                    start_row_index=11,
                    end_row_index=13,
                    start_column_index=0,
                    end_column_index=1,
                ),
            ),
            fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
        )

    with pytest.raises(ConfiguredReadContractError, match="CONFIG_FIELDS_UNAPPROVED"):
        ConfiguredReadPlan(
            spreadsheet_id="synthetic",
            config_version="v1",
            sheets=(sheet,),
            ranges=(valid,),
            fields=("formattedValue",),
        )


def test_unsupported_response_shape_is_not_accepted_as_coverage():
    payload = _response()
    payload["values"] = [["not a spreadsheets.get response"]]

    with pytest.raises(GoogleSheetsResponseError) as caught:
        map_google_sheets_response(payload, _plan())

    assert caught.value.code == "GOOGLE_RESPONSE_SHAPE_UNSUPPORTED"


def test_configured_result_cannot_be_publicly_reconstructed_or_mutated():
    result = map_google_sheets_response(_response(), _plan())

    with pytest.raises(TypeError, match="CONFIGURED_READ_RESULT_CONSTRUCTION_FORBIDDEN"):
        ConfiguredReadResult()

    with pytest.raises(AttributeError):
        result.snapshot = result.snapshot

    assert result.coverage_proof.configuration_identity == result.configuration_identity


def test_plan_derives_exact_a1_selection_and_repr_hides_target():
    plan = _plan()

    assert plan.request_ranges == (
        "'Synthetic Coverage'!B2:C3",
        "'Synthetic Coverage'!D6:E7",
    )
    assert plan.spreadsheet_id not in repr(plan)
