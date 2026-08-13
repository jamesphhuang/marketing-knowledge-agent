from __future__ import annotations

import json
from copy import deepcopy

import pytest

from marketing_knowledge_agent.canonical_serialization import (
    compute_source_fingerprint,
    serialize_source_snapshot,
)
from marketing_knowledge_agent.sheets_contracts import SpreadsheetSnapshot
from sprint0_fixtures import load_synthetic_json


def _payload():
    return load_synthetic_json("cell_data_wp1.json")


def _snapshot(payload=None) -> SpreadsheetSnapshot:
    return SpreadsheetSnapshot(**(payload or _payload()))


def _fingerprint_after(mutate) -> str:
    payload = _payload()
    mutate(payload)
    return compute_source_fingerprint(_snapshot(payload))


def _assert_same_serialization_and_fingerprint(original, reordered):
    original_snapshot = _snapshot(original)
    reordered_snapshot = _snapshot(reordered)

    assert serialize_source_snapshot(original_snapshot) == serialize_source_snapshot(
        reordered_snapshot
    )
    assert compute_source_fingerprint(original_snapshot) == compute_source_fingerprint(
        reordered_snapshot
    )


def test_same_snapshot_has_stable_canonical_bytes_and_fingerprint():
    snapshot = _snapshot()

    first_bytes = serialize_source_snapshot(snapshot)
    second_bytes = serialize_source_snapshot(snapshot)
    first_fingerprint = compute_source_fingerprint(snapshot)
    second_fingerprint = compute_source_fingerprint(snapshot)

    assert first_bytes == second_bytes
    assert first_fingerprint == second_fingerprint
    assert first_fingerprint.startswith("sha256:")
    assert len(first_fingerprint) == len("sha256:") + 64


def test_payload_safe_repr_does_not_change_serialization_or_fingerprint():
    snapshot = _snapshot()
    serialized_before = serialize_source_snapshot(snapshot)
    fingerprint_before = compute_source_fingerprint(snapshot)

    rendered = repr(snapshot)

    assert "Synthetic Plain Text" not in rendered
    assert serialize_source_snapshot(snapshot) == serialized_before
    assert compute_source_fingerprint(snapshot) == fingerprint_before


def test_synthetic_snapshot_matches_golden_fingerprint():
    assert compute_source_fingerprint(_snapshot()) == (
        "sha256:8f3b71a0b419b35245ddfc0aa1a12327eeadc143aa4a40a8c48aceae4c5c25d9"
    )


def test_mapping_key_order_does_not_change_canonical_bytes():
    original = _payload()
    reordered = dict(reversed(list(original.items())))
    reordered["sheets"] = [
        dict(reversed(list(sheet.items()))) for sheet in reordered["sheets"]
    ]
    reordered["sheets"][0]["cells"] = [
        dict(reversed(list(cell.items())))
        for cell in reordered["sheets"][0]["cells"]
    ]

    assert serialize_source_snapshot(_snapshot(original)) == serialize_source_snapshot(
        _snapshot(reordered)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["sheets"][0]["cells"][0].__setitem__(
            "formatted_value", "Synthetic changed display"
        ),
        lambda payload: payload["sheets"][0]["cells"][1]["effective_value"].__setitem__(
            "number_value", 43
        ),
        lambda payload: payload["sheets"][0]["cells"][3]["user_entered_value"].__setitem__(
            "formula_value", "=SUM(B1,9)"
        ),
        lambda payload: payload["sheets"][0]["cells"][4].__setitem__(
            "column_index", 3
        ),
        lambda payload: payload["sheets"][0]["cells"][4].__setitem__(
            "hyperlink", "https://example.net/synthetic-changed-whole-cell"
        ),
        lambda payload: payload["sheets"][0]["cells"][5]["text_format_runs"][0][
            "link"
        ].__setitem__("uri", "https://example.com/synthetic-changed-run"),
        lambda payload: payload["sheets"][0]["cells"][5]["text_format_runs"][1].__setitem__(
            "start_index", 15
        ),
        lambda payload: payload["sheets"][0]["cells"][2]["data_validation"][
            "condition"
        ].__setitem__("condition_type", "TEXT_EQ"),
        lambda payload: payload["sheets"][0]["merges"][0].__setitem__(
            "end_row_index", 6
        ),
        lambda payload: payload["sheets"][0].__setitem__(
            "title", "Synthetic Inputs Changed"
        ),
    ],
    ids=[
        "formatted-value",
        "effective-value",
        "user-entered-formula",
        "cell-coordinate",
        "whole-cell-hyperlink",
        "text-run-link",
        "text-run-offset",
        "data-validation",
        "merge-range",
        "sheet-title",
    ],
)
def test_source_field_changes_change_fingerprint(mutate):
    assert _fingerprint_after(mutate) != compute_source_fingerprint(_snapshot())


def test_sheet_id_change_changes_fingerprint():
    def change_sheet_id(payload):
        payload["sheets"][0]["sheet_id"] = 303
        payload["sheets"][0]["merges"][0]["sheet_id"] = 303

    assert _fingerprint_after(change_sheet_id) != compute_source_fingerprint(_snapshot())


def test_unicode_is_utf8_and_deterministic_without_escape_or_normalization():
    snapshot = _snapshot()
    serialized = serialize_source_snapshot(snapshot)

    assert serialized == serialize_source_snapshot(snapshot)
    assert "虛構資料 α".encode("utf-8") in serialized
    assert b"\\u865b" not in serialized

    payload = _payload()
    payload["sheets"][1]["cells"][0]["formatted_value"] = "e\u0301"
    decomposed = serialize_source_snapshot(_snapshot(payload))
    payload["sheets"][1]["cells"][0]["formatted_value"] = "\u00e9"
    composed = serialize_source_snapshot(_snapshot(payload))
    assert decomposed != composed


def test_absent_and_explicit_optional_defaults_have_same_representation():
    absent = _payload()
    explicit = deepcopy(absent)
    explicit["sheets"][0]["cells"][6].update(
        {
            "formatted_value": None,
            "effective_value": None,
            "user_entered_value": None,
            "hyperlink": None,
            "text_format_runs": [],
            "data_validation": None,
        }
    )

    assert serialize_source_snapshot(_snapshot(absent)) == serialize_source_snapshot(
        _snapshot(explicit)
    )


def test_finite_float_is_preserved():
    payload = _payload()
    payload["sheets"][0]["cells"][1]["effective_value"]["number_value"] = 42.5
    finite = _snapshot(payload)

    serialized = serialize_source_snapshot(finite)
    decoded = json.loads(serialized.decode("utf-8"))

    assert decoded["sheets"][0]["cells"][1]["effective_value"]["number_value"] == 42.5
    assert serialized == serialize_source_snapshot(finite)


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_non_finite_float_is_rejected(non_finite):
    payload = _payload()
    payload["sheets"][0]["cells"][1]["effective_value"]["number_value"] = non_finite

    with pytest.raises(ValueError, match="SOURCE_SNAPSHOT_FLOAT_NON_FINITE"):
        serialize_source_snapshot(_snapshot(payload))


def test_sheet_input_order_does_not_change_serialization_or_fingerprint():
    original = _payload()
    reordered = deepcopy(original)
    reordered["sheets"].reverse()

    _assert_same_serialization_and_fingerprint(original, reordered)


def test_cell_input_order_does_not_change_serialization_or_fingerprint():
    original = _payload()
    reordered = deepcopy(original)
    reordered["sheets"][0]["cells"].reverse()

    _assert_same_serialization_and_fingerprint(original, reordered)


def test_merge_input_order_does_not_change_serialization_or_fingerprint():
    original = _payload()
    original["sheets"][0]["merges"].append(
        {
            "sheet_id": 101,
            "start_row_index": 5,
            "end_row_index": 7,
            "start_column_index": 1,
            "end_column_index": 2,
        }
    )
    reordered = deepcopy(original)
    reordered["sheets"][0]["merges"].reverse()

    _assert_same_serialization_and_fingerprint(original, reordered)


def test_serialization_preserves_formula_links_validation_merges_and_all_sheets():
    payload = json.loads(serialize_source_snapshot(_snapshot()).decode("utf-8"))
    first_sheet = payload["sheets"][0]

    assert payload["spreadsheet_id"] == "synthetic-spreadsheet-wp1"
    assert [sheet["sheet_id"] for sheet in payload["sheets"]] == [101, 202]
    assert first_sheet["cells"][3]["formatted_value"] == "50"
    assert first_sheet["cells"][3]["effective_value"]["number_value"] == 50
    assert first_sheet["cells"][3]["user_entered_value"]["formula_value"] == "=SUM(B1,8)"
    assert first_sheet["cells"][4]["hyperlink"].endswith("synthetic-whole-cell")
    assert first_sheet["cells"][5]["text_format_runs"][2]["start_index"] == 23
    assert first_sheet["cells"][2]["data_validation"]["strict"] is True
    assert first_sheet["merges"][0]["end_row_index"] == 5


def test_serializer_does_not_mutate_input_dto():
    payload = _payload()
    payload["sheets"][0]["cells"].reverse()
    payload["sheets"].reverse()
    snapshot = _snapshot(payload)
    baseline = _snapshot(payload)

    serialize_source_snapshot(snapshot)
    compute_source_fingerprint(snapshot)

    assert snapshot == baseline


def test_serializer_has_no_filesystem_or_network_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("WP2 serializer attempted an external side effect")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.getaddrinfo", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)

    assert serialize_source_snapshot(_snapshot())
    assert compute_source_fingerprint(_snapshot()).startswith("sha256:")
