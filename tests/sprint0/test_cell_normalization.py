from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Optional, Tuple

import pytest

from marketing_knowledge_agent.cell_normalization import (
    CellNormalizationError,
    FieldContract,
    FieldValueKind,
    InheritanceReason,
    ValueSource,
    normalize_source_cell,
)
from marketing_knowledge_agent.sheets_contracts import (
    CellData,
    DataValidation,
    DataValidationCondition,
    GoogleValue,
    GridRange,
    SheetSnapshot,
    SpreadsheetSnapshot,
    TextFormatLink,
    TextFormatRun,
)


def _cell(
    row: int,
    column: int,
    *,
    formatted: Optional[str] = None,
    effective: Optional[GoogleValue] = None,
    entered: Optional[GoogleValue] = None,
    hyperlink: Optional[str] = None,
    runs: Tuple[TextFormatRun, ...] = (),
    validation: Optional[DataValidation] = None,
) -> CellData:
    return CellData(
        row_index=row,
        column_index=column,
        formatted_value=formatted,
        effective_value=effective,
        user_entered_value=entered,
        hyperlink=hyperlink,
        text_format_runs=runs,
        data_validation=validation,
    )


def _sheet(
    *,
    sheet_id: int = 101,
    title: str = "Synthetic Source",
    hidden: bool = False,
    cells: Tuple[CellData, ...] = (),
    merges: Tuple[GridRange, ...] = (),
) -> SheetSnapshot:
    return SheetSnapshot(
        sheet_id=sheet_id,
        title=title,
        hidden=hidden,
        row_count=8,
        column_count=6,
        cells=cells,
        merges=merges,
    )


def _snapshot(*sheets: SheetSnapshot) -> SpreadsheetSnapshot:
    return SpreadsheetSnapshot(
        spreadsheet_id="synthetic-spreadsheet-wp3",
        sheets=sheets or (_sheet(),),
    )


def _contract(
    column: int = 0,
    *,
    kind: FieldValueKind = FieldValueKind.TEXT,
    merge: bool = False,
) -> FieldContract:
    return FieldContract(
        field_name=f"synthetic_field_{column}",
        value_kind=kind,
        source_column_index=column,
        merge_inheritance_allowed=merge,
    )


def test_plain_unmerged_text_uses_formatted_value_and_preserves_raw_source_fields():
    source = _cell(
        0,
        0,
        formatted="Synthetic display text",
        effective=GoogleValue(string_value="Synthetic effective text"),
        entered=GoogleValue(string_value="Synthetic entered text"),
    )
    snapshot = _snapshot(_sheet(cells=(source,)))

    resolved = normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
        source_fingerprint="sha256:synthetic-source-fingerprint",
        sync_batch_id="SYNTHETIC-BATCH-WP3",
    )

    assert resolved.normalized_value == "Synthetic display text"
    assert resolved.display_value == "Synthetic display text"
    assert resolved.value_source is ValueSource.FORMATTED_VALUE
    assert resolved.source_cell is source
    assert resolved.value_cell is source
    assert resolved.source_cell.effective_value.string_value == "Synthetic effective text"
    assert resolved.source_cell.user_entered_value.string_value == "Synthetic entered text"
    assert resolved.lineage.spreadsheet_id == "synthetic-spreadsheet-wp3"
    assert resolved.lineage.source_fingerprint == "sha256:synthetic-source-fingerprint"
    assert resolved.lineage.sync_batch_id == "SYNTHETIC-BATCH-WP3"


def test_formula_uses_effective_typed_value_and_never_formula_expression_as_content():
    formula = _cell(
        1,
        2,
        formatted="1,250.5",
        effective=GoogleValue(number_value=1250.5),
        entered=GoogleValue(formula_value="=SUM(C1:C2)"),
    )
    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(formula,))),
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(2, kind=FieldValueKind.NUMBER),
    )

    assert resolved.normalized_value == 1250.5
    assert resolved.display_value == "1,250.5"
    assert resolved.value_source is ValueSource.EFFECTIVE_VALUE
    assert resolved.source_was_formula is True
    assert resolved.value_cell.user_entered_value.formula_value == "=SUM(C1:C2)"
    assert resolved.normalized_value != "=SUM(C1:C2)"
    assert resolved.display_value != "=SUM(C1:C2)"
    assert "=SUM(C1:C2)" not in repr(resolved)


def test_text_formula_prefers_formatted_display_over_effective_string():
    formula = _cell(
        0,
        0,
        formatted="Synthetic formatted result",
        effective=GoogleValue(string_value="Synthetic effective result"),
        entered=GoogleValue(formula_value='="Synthetic formula expression"'),
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(formula,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert resolved.normalized_value == "Synthetic formatted result"
    assert resolved.value_source is ValueSource.FORMATTED_VALUE
    assert resolved.source_was_formula is True


@pytest.mark.parametrize(
    ("effective", "expected"),
    [
        (GoogleValue(string_value="Synthetic fallback"), "Synthetic fallback"),
        (GoogleValue(number_value=42.5), "42.5"),
        (GoogleValue(bool_value=True), "TRUE"),
    ],
    ids=("string", "number", "boolean"),
)
def test_text_without_formatted_value_uses_type_safe_effective_fallback(effective, expected):
    source = _cell(0, 0, effective=effective)

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(source,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert resolved.normalized_value == expected
    assert resolved.display_value == expected
    assert resolved.value_source is ValueSource.EFFECTIVE_VALUE_TEXT_FALLBACK


def test_merged_anchor_is_local_value_with_merge_range_lineage():
    merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    anchor = _cell(
        0,
        0,
        formatted="Synthetic merged anchor",
        effective=GoogleValue(string_value="Synthetic merged anchor"),
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(anchor,), merges=(merge,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(merge=True),
    )

    assert resolved.normalized_value == "Synthetic merged anchor"
    assert resolved.field_lineage.inheritance_reason is InheritanceReason.MERGE_ANCHOR
    assert resolved.field_lineage.inherited_from_merge is False
    assert resolved.field_lineage.merge_anchor_coordinate == (0, 0)
    assert resolved.field_lineage.merge_range is merge


def test_covered_merged_cell_inherits_only_from_anchor_and_preserves_both_coordinates():
    merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    anchor = _cell(
        0,
        0,
        formatted="Synthetic merge-only value",
        effective=GoogleValue(string_value="Synthetic merge-only value"),
    )
    covered = _cell(1, 0)

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(anchor, covered), merges=(merge,))),
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(merge=True),
    )

    assert resolved.normalized_value == "Synthetic merge-only value"
    assert resolved.source_cell is covered
    assert resolved.value_cell is anchor
    assert resolved.lineage.source_coordinate == (1, 0)
    assert resolved.field_lineage.target_coordinate == (1, 0)
    assert resolved.field_lineage.value_coordinate == (0, 0)
    assert resolved.field_lineage.merge_anchor_coordinate == (0, 0)
    assert resolved.field_lineage.merge_range is merge
    assert resolved.field_lineage.inherited_from_merge is True
    assert resolved.field_lineage.inheritance_reason is InheritanceReason.MERGED_RANGE


def test_same_visual_blank_without_merge_metadata_does_not_inherit():
    previous = _cell(
        0,
        0,
        formatted="Synthetic Category",
        effective=GoogleValue(string_value="Synthetic Category"),
    )
    blank = _cell(1, 0)

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(previous, blank))),
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(merge=True),
    )

    assert resolved.normalized_value is None
    assert resolved.value_source is ValueSource.BLANK
    assert resolved.value_cell is blank
    assert resolved.field_lineage.inherited_from_merge is False
    assert resolved.field_lineage.inheritance_reason is InheritanceReason.LOCAL


def test_adjacent_previous_value_never_blind_fills_down():
    previous = _cell(
        3,
        1,
        formatted="Synthetic previous-row value",
        effective=GoogleValue(string_value="Synthetic previous-row value"),
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(previous,))),
        sheet_id=101,
        source_row_index=4,
        field_contract=_contract(1, merge=True),
    )

    assert resolved.normalized_value is None
    assert resolved.source_cell is None
    assert resolved.value_cell is None
    assert resolved.field_lineage.value_coordinate == (4, 1)


def test_multiple_independent_merges_do_not_leak_across_ranges():
    first_merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    second_merge = GridRange(
        sheet_id=101,
        start_row_index=2,
        end_row_index=4,
        start_column_index=0,
        end_column_index=1,
    )
    first = _cell(0, 0, formatted="Synthetic first", effective=GoogleValue(string_value="Synthetic first"))
    second = _cell(2, 0, formatted="Synthetic second", effective=GoogleValue(string_value="Synthetic second"))
    snapshot = _snapshot(_sheet(cells=(first, second), merges=(first_merge, second_merge)))

    first_covered = normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(merge=True),
    )
    second_covered = normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=3,
        field_contract=_contract(merge=True),
    )

    assert first_covered.normalized_value == "Synthetic first"
    assert first_covered.field_lineage.merge_anchor_coordinate == (0, 0)
    assert second_covered.normalized_value == "Synthetic second"
    assert second_covered.field_lineage.merge_anchor_coordinate == (2, 0)


def test_merge_across_columns_maps_each_covered_target_to_top_left_anchor():
    merge = GridRange(
        sheet_id=101,
        start_row_index=2,
        end_row_index=4,
        start_column_index=1,
        end_column_index=3,
    )
    anchor = _cell(2, 1, formatted="Synthetic wide merge", effective=GoogleValue(string_value="Synthetic wide merge"))
    snapshot = _snapshot(_sheet(cells=(anchor,), merges=(merge,)))

    resolved = normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=3,
        field_contract=_contract(2, merge=True),
    )

    assert resolved.normalized_value == "Synthetic wide merge"
    assert resolved.lineage.source_coordinate == (3, 2)
    assert resolved.field_lineage.value_coordinate == (2, 1)
    assert resolved.field_lineage.merge_anchor_coordinate == (2, 1)


def test_blank_merge_anchor_does_not_invent_a_nonblank_value():
    merge = GridRange(
        sheet_id=101,
        start_row_index=1,
        end_row_index=3,
        start_column_index=0,
        end_column_index=1,
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(merges=(merge,))),
        sheet_id=101,
        source_row_index=2,
        field_contract=_contract(merge=True),
    )

    assert resolved.normalized_value is None
    assert resolved.display_value is None
    assert resolved.value_source is ValueSource.BLANK
    assert resolved.field_lineage.inherited_from_merge is True
    assert resolved.field_lineage.merge_anchor_coordinate == (1, 0)


def test_boolean_checkbox_retains_source_state_without_governance_inference():
    checkbox = _cell(
        0,
        3,
        formatted="TRUE",
        effective=GoogleValue(bool_value=True),
        entered=GoogleValue(bool_value=True),
        validation=DataValidation(
            condition=DataValidationCondition(condition_type="BOOLEAN")
        ),
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(checkbox,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(3, kind=FieldValueKind.BOOLEAN),
    )

    assert resolved.normalized_value is True
    assert resolved.display_value == "TRUE"
    assert resolved.source_cell.data_validation.condition.condition_type == "BOOLEAN"
    assert not hasattr(resolved, "oral_only")
    assert not hasattr(resolved, "allowed_exposure_channels")
    assert not hasattr(resolved, "searchable")


def test_boolean_type_or_validation_conflict_raises_stable_redacted_error():
    mismatch = _cell(
        0,
        0,
        formatted="Synthetic not boolean",
        effective=GoogleValue(string_value="Synthetic secret body"),
        validation=DataValidation(
            condition=DataValidationCondition(condition_type="BOOLEAN")
        ),
    )

    with pytest.raises(CellNormalizationError) as exc_info:
        normalize_source_cell(
            _snapshot(_sheet(cells=(mismatch,))),
            sheet_id=101,
            source_row_index=0,
            field_contract=_contract(kind=FieldValueKind.BOOLEAN),
        )

    assert str(exc_info.value) == "CELL_EFFECTIVE_TYPE_MISMATCH"
    assert "Synthetic secret body" not in repr(exc_info.value)


def test_whole_cell_hyperlink_and_text_format_runs_are_preserved_not_extracted():
    runs = (
        TextFormatRun(start_index=0, link=TextFormatLink(uri="https://example.org/synthetic-run")),
        TextFormatRun(start_index=9),
    )
    source = _cell(
        0,
        0,
        formatted="Synthetic linked title",
        effective=GoogleValue(string_value="Synthetic linked title"),
        hyperlink="https://example.com/synthetic-whole-cell",
        runs=runs,
    )

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(source,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert resolved.source_cell.hyperlink == "https://example.com/synthetic-whole-cell"
    assert resolved.source_cell.text_format_runs == runs
    assert not hasattr(resolved, "url_candidates")
    assert not hasattr(resolved, "canonical_url")


def test_lineage_is_sheet_specific_for_visible_and_hidden_sheets():
    visible_cell = _cell(0, 0, formatted="Synthetic visible", effective=GoogleValue(string_value="Synthetic visible"))
    hidden_cell = _cell(0, 0, formatted="Synthetic hidden", effective=GoogleValue(string_value="Synthetic hidden"))
    snapshot = _snapshot(
        _sheet(sheet_id=101, title="Synthetic Visible", cells=(visible_cell,)),
        _sheet(sheet_id=202, title="Synthetic Hidden", hidden=True, cells=(hidden_cell,)),
    )

    visible = normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )
    hidden = normalize_source_cell(
        snapshot,
        sheet_id=202,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert (visible.lineage.sheet_id, visible.lineage.sheet_title, visible.lineage.sheet_hidden) == (
        101,
        "Synthetic Visible",
        False,
    )
    assert (hidden.lineage.sheet_id, hidden.lineage.sheet_title, hidden.lineage.sheet_hidden) == (
        202,
        "Synthetic Hidden",
        True,
    )
    assert hidden.normalized_value == "Synthetic hidden"
    assert not hasattr(hidden, "hidden_sheet_eligibility")


def test_merge_inheritance_must_be_explicitly_allowed_by_field_contract():
    merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    anchor = _cell(0, 0, formatted="Synthetic anchor", effective=GoogleValue(string_value="Synthetic anchor"))

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(anchor,), merges=(merge,))),
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(merge=False),
    )

    assert resolved.normalized_value is None
    assert resolved.field_lineage.inherited_from_merge is False
    assert resolved.field_lineage.inheritance_reason is InheritanceReason.MERGE_INHERITANCE_DISALLOWED
    assert resolved.field_lineage.merge_anchor_coordinate == (0, 0)


def test_covered_merged_cell_with_conflicting_source_value_is_rejected():
    merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    anchor = _cell(0, 0, formatted="Synthetic anchor", effective=GoogleValue(string_value="Synthetic anchor"))
    conflict = _cell(1, 0, formatted="Synthetic conflict", effective=GoogleValue(string_value="Synthetic conflict"))

    with pytest.raises(CellNormalizationError, match="MERGE_COVERED_CELL_SOURCE_CONFLICT"):
        normalize_source_cell(
            _snapshot(_sheet(cells=(anchor, conflict), merges=(merge,))),
            sheet_id=101,
            source_row_index=1,
            field_contract=_contract(merge=True),
        )


def test_formula_missing_effective_cache_is_rejected_without_formula_text_in_error():
    formula = _cell(
        0,
        0,
        formatted="Synthetic stale display",
        entered=GoogleValue(formula_value='="Synthetic sensitive expression"'),
    )

    with pytest.raises(CellNormalizationError) as exc_info:
        normalize_source_cell(
            _snapshot(_sheet(cells=(formula,))),
            sheet_id=101,
            source_row_index=0,
            field_contract=_contract(),
        )

    assert str(exc_info.value) == "CELL_FORMULA_EFFECTIVE_VALUE_MISSING"
    assert "Synthetic sensitive expression" not in repr(exc_info.value)


def test_normalization_does_not_mutate_snapshot():
    merge = GridRange(
        sheet_id=101,
        start_row_index=0,
        end_row_index=2,
        start_column_index=0,
        end_column_index=1,
    )
    anchor = _cell(0, 0, formatted="Synthetic immutable", effective=GoogleValue(string_value="Synthetic immutable"))
    snapshot = _snapshot(_sheet(cells=(anchor,), merges=(merge,)))
    baseline = deepcopy(snapshot)

    normalize_source_cell(
        snapshot,
        sheet_id=101,
        source_row_index=1,
        field_contract=_contract(merge=True),
    )

    assert snapshot == baseline


def test_normalized_output_is_immutable():
    source = _cell(0, 0, formatted="Synthetic immutable output")
    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(source,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    with pytest.raises(FrozenInstanceError):
        resolved.normalized_value = "Synthetic mutation"


def test_normalization_has_no_filesystem_or_network_side_effects(monkeypatch):
    source = _cell(0, 0, formatted="Synthetic pure value", effective=GoogleValue(string_value="Synthetic pure value"))

    def unexpected(*args, **kwargs):
        raise AssertionError("WP3 normalization attempted an external side effect")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.getaddrinfo", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(source,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert resolved.normalized_value == "Synthetic pure value"


def test_transient_oral_only_like_text_has_no_persistence_preview_or_log_surface(caplog):
    sentinel = "SYNTHETIC ORAL ONLY WP3 SENTINEL"
    source = _cell(0, 0, formatted=sentinel, effective=GoogleValue(string_value=sentinel))

    resolved = normalize_source_cell(
        _snapshot(_sheet(cells=(source,))),
        sheet_id=101,
        source_row_index=0,
        field_contract=_contract(),
    )

    assert resolved.normalized_value == sentinel
    assert sentinel not in repr(resolved)
    assert sentinel not in caplog.text
    assert not hasattr(resolved, "model_dump")
    assert not hasattr(resolved, "model_dump_json")
    for method_name in ("save", "write", "persist", "index", "render", "preview"):
        assert not hasattr(resolved, method_name)
    with pytest.raises(TypeError):
        json.dumps(resolved)


def test_invalid_target_coordinate_fails_with_stable_error_code():
    with pytest.raises(CellNormalizationError, match="SOURCE_CELL_COORDINATE_OUT_OF_BOUNDS"):
        normalize_source_cell(
            _snapshot(),
            sheet_id=101,
            source_row_index=8,
            field_contract=_contract(),
        )
