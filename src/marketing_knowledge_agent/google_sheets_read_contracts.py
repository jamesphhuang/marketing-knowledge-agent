"""Immutable WP0 configuration, coverage, and configured-read contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Tuple

from .sheets_contracts import SpreadsheetSnapshot


MAPPER_VERSION = "google-response-mapper-v1"
REQUIRED_GOOGLE_RESPONSE_FIELDS = (
    "spreadsheetId",
    "sheets.properties.sheetId",
    "sheets.properties.title",
    "sheets.properties.hidden",
    "sheets.properties.gridProperties.rowCount",
    "sheets.properties.gridProperties.columnCount",
    "sheets.data.startRow",
    "sheets.data.startColumn",
    "sheets.data.rowData.values.formattedValue",
    "sheets.data.rowData.values.effectiveValue",
    "sheets.data.rowData.values.userEnteredValue",
    "sheets.data.rowData.values.hyperlink",
    "sheets.data.rowData.values.textFormatRuns.startIndex",
    "sheets.data.rowData.values.textFormatRuns.format.link",
    "sheets.data.rowData.values.dataValidation",
    "sheets.merges",
)

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConfiguredReadContractError(ValueError):
    """Payload-safe validation failure for a configured read plan."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


@dataclass(frozen=True)
class ConfiguredSheet:
    sheet_id: int
    title: str
    hidden: bool
    row_count: int
    column_count: int

    def __post_init__(self) -> None:
        if not _is_int(self.sheet_id) or self.sheet_id < 0:
            _contract_error("CONFIG_SHEET_ID_INVALID")
        if not isinstance(self.title, str) or not self.title:
            _contract_error("CONFIG_SHEET_TITLE_INVALID")
        if type(self.hidden) is not bool:
            _contract_error("CONFIG_SHEET_HIDDEN_INVALID")
        if not _is_int(self.row_count) or self.row_count <= 0:
            _contract_error("CONFIG_SHEET_ROW_COUNT_INVALID")
        if not _is_int(self.column_count) or self.column_count <= 0:
            _contract_error("CONFIG_SHEET_COLUMN_COUNT_INVALID")


@dataclass(frozen=True)
class ConfiguredRange:
    range_id: str
    sheet_id: int
    start_row_index: int
    end_row_index: int
    start_column_index: int
    end_column_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.range_id, str) or not _SAFE_ID_PATTERN.fullmatch(
            self.range_id
        ):
            _contract_error("CONFIG_RANGE_ID_INVALID")
        if not _is_int(self.sheet_id) or self.sheet_id < 0:
            _contract_error("CONFIG_RANGE_SHEET_ID_INVALID")
        bounds = (
            self.start_row_index,
            self.end_row_index,
            self.start_column_index,
            self.end_column_index,
        )
        if any(not _is_int(value) or value < 0 for value in bounds):
            _contract_error("CONFIG_RANGE_BOUNDS_INVALID")
        if self.end_row_index <= self.start_row_index:
            _contract_error("CONFIG_RANGE_BOUNDS_INVALID")
        if self.end_column_index <= self.start_column_index:
            _contract_error("CONFIG_RANGE_BOUNDS_INVALID")


@dataclass(frozen=True)
class ConfiguredReadPlan:
    """Explicit synthetic/mock selection contract consumed by the WP0 mapper."""

    spreadsheet_id: str
    config_version: str
    sheets: Tuple[ConfiguredSheet, ...]
    ranges: Tuple[ConfiguredRange, ...]
    fields: Tuple[str, ...]
    method: str = "spreadsheets.get"
    include_grid_data: bool = True
    mapper_version: str = MAPPER_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.spreadsheet_id, str) or not self.spreadsheet_id:
            _contract_error("CONFIG_TARGET_INVALID")
        if not _is_safe_id(self.config_version):
            _contract_error("CONFIG_VERSION_INVALID")
        if self.method != "spreadsheets.get":
            _contract_error("CONFIG_METHOD_UNAPPROVED")
        if self.include_grid_data is not True:
            _contract_error("CONFIG_GRID_DATA_REQUIRED")
        if self.mapper_version != MAPPER_VERSION:
            _contract_error("CONFIG_MAPPER_VERSION_UNAPPROVED")
        if type(self.sheets) is not tuple or not self.sheets:
            _contract_error("CONFIG_SHEETS_MISSING")
        if type(self.ranges) is not tuple or not self.ranges:
            _contract_error("CONFIG_RANGES_MISSING")
        if type(self.fields) is not tuple or self.fields != REQUIRED_GOOGLE_RESPONSE_FIELDS:
            _contract_error("CONFIG_FIELDS_UNAPPROVED")
        if any(not isinstance(item, ConfiguredSheet) for item in self.sheets):
            _contract_error("CONFIG_SHEET_TYPE_INVALID")
        if any(not isinstance(item, ConfiguredRange) for item in self.ranges):
            _contract_error("CONFIG_RANGE_TYPE_INVALID")

        sheet_ids = [sheet.sheet_id for sheet in self.sheets]
        sheet_titles = [sheet.title for sheet in self.sheets]
        if len(sheet_ids) != len(set(sheet_ids)):
            _contract_error("CONFIG_SHEET_ID_DUPLICATE")
        if len(sheet_titles) != len(set(sheet_titles)):
            _contract_error("CONFIG_SHEET_TITLE_DUPLICATE")

        range_ids = [configured_range.range_id for configured_range in self.ranges]
        if len(range_ids) != len(set(range_ids)):
            _contract_error("CONFIG_RANGE_ID_DUPLICATE")

        sheets_by_id = {sheet.sheet_id: sheet for sheet in self.sheets}
        referenced_sheet_ids = set()
        for configured_range in self.ranges:
            sheet = sheets_by_id.get(configured_range.sheet_id)
            if sheet is None:
                _contract_error("CONFIG_RANGE_SHEET_MISSING")
            referenced_sheet_ids.add(configured_range.sheet_id)
            if (
                configured_range.end_row_index > sheet.row_count
                or configured_range.end_column_index > sheet.column_count
            ):
                _contract_error("CONFIG_RANGE_OUT_OF_BOUNDS")

        if referenced_sheet_ids != set(sheet_ids):
            _contract_error("CONFIG_SHEET_RANGE_MISSING")

        for index, left in enumerate(self.ranges):
            for right in self.ranges[index + 1 :]:
                if _configured_ranges_overlap(left, right):
                    _contract_error("CONFIG_RANGES_OVERLAP")

    @property
    def configuration_identity(self) -> str:
        payload = {
            "config_version": self.config_version,
            "fields": self.fields,
            "include_grid_data": self.include_grid_data,
            "mapper_version": self.mapper_version,
            "method": self.method,
            "ranges": [
                {
                    "end_column_index": value.end_column_index,
                    "end_row_index": value.end_row_index,
                    "range_id": value.range_id,
                    "sheet_id": value.sheet_id,
                    "start_column_index": value.start_column_index,
                    "start_row_index": value.start_row_index,
                }
                for value in sorted(self.ranges, key=lambda item: item.range_id)
            ],
            "sheets": [
                {
                    "column_count": value.column_count,
                    "hidden": value.hidden,
                    "row_count": value.row_count,
                    "sheet_id": value.sheet_id,
                    "title": value.title,
                }
                for value in sorted(self.sheets, key=lambda item: item.sheet_id)
            ],
            "spreadsheet_id": self.spreadsheet_id,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(serialized).hexdigest()}"

    @property
    def request_ranges(self) -> Tuple[str, ...]:
        sheets_by_id = {sheet.sheet_id: sheet for sheet in self.sheets}
        return tuple(
            _a1_range(sheets_by_id[value.sheet_id].title, value)
            for value in self.ranges
        )

    def __repr__(self) -> str:
        return (
            "ConfiguredReadPlan("
            f"config_version={self.config_version!r}, "
            f"configuration_identity={self.configuration_identity!r}, "
            f"range_count={len(self.ranges)})"
        )


@dataclass(frozen=True)
class CoveredRange:
    range_id: str
    sheet_id: int
    start_row_index: int
    end_row_index: int
    start_column_index: int
    end_column_index: int


@dataclass(frozen=True)
class ConfiguredRangeCoverageProof:
    configuration_identity: str
    config_version: str
    mapper_version: str
    method: str
    include_grid_data: bool
    expected_range_count: int
    observed_range_count: int
    covered_ranges: Tuple[CoveredRange, ...]


class ConfiguredReadResult:
    """Opaque, immutable binding of raw snapshot, coverage, and config identity."""

    __slots__ = ("_snapshot", "_coverage_proof", "_configuration_identity")

    def __init__(self, *args, **kwargs):
        raise TypeError("CONFIGURED_READ_RESULT_CONSTRUCTION_FORBIDDEN")

    @property
    def snapshot(self) -> SpreadsheetSnapshot:
        return self._snapshot

    @property
    def coverage_proof(self) -> ConfiguredRangeCoverageProof:
        return self._coverage_proof

    @property
    def configuration_identity(self) -> str:
        return self._configuration_identity

    def __setattr__(self, name, value) -> None:
        raise AttributeError("CONFIGURED_READ_RESULT_IMMUTABLE")

    def __repr__(self) -> str:
        proof = self.coverage_proof
        return (
            "ConfiguredReadResult("
            f"configuration_identity={self.configuration_identity!r}, "
            f"config_version={proof.config_version!r}, "
            f"coverage={proof.observed_range_count}/{proof.expected_range_count})"
        )


def _create_configured_read_result(
    snapshot: SpreadsheetSnapshot,
    coverage_proof: ConfiguredRangeCoverageProof,
    configuration_identity: str,
) -> ConfiguredReadResult:
    if not isinstance(snapshot, SpreadsheetSnapshot):
        raise TypeError("CONFIGURED_READ_RESULT_SNAPSHOT_INVALID")
    if not isinstance(coverage_proof, ConfiguredRangeCoverageProof):
        raise TypeError("CONFIGURED_READ_RESULT_COVERAGE_INVALID")
    if coverage_proof.configuration_identity != configuration_identity:
        raise ValueError("CONFIGURED_READ_RESULT_BINDING_MISMATCH")
    result = object.__new__(ConfiguredReadResult)
    object.__setattr__(result, "_snapshot", snapshot)
    object.__setattr__(result, "_coverage_proof", coverage_proof)
    object.__setattr__(result, "_configuration_identity", configuration_identity)
    return result


def _contract_error(code: str) -> None:
    raise ConfiguredReadContractError(code)


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_safe_id(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_ID_PATTERN.fullmatch(value))


def _configured_ranges_overlap(left: ConfiguredRange, right: ConfiguredRange) -> bool:
    if left.sheet_id != right.sheet_id:
        return False
    rows_overlap = (
        left.start_row_index < right.end_row_index
        and right.start_row_index < left.end_row_index
    )
    columns_overlap = (
        left.start_column_index < right.end_column_index
        and right.start_column_index < left.end_column_index
    )
    return rows_overlap and columns_overlap


def _a1_range(title: str, configured_range: ConfiguredRange) -> str:
    escaped_title = title.replace("'", "''")
    start_column = _column_label(configured_range.start_column_index)
    end_column = _column_label(configured_range.end_column_index - 1)
    start_row = configured_range.start_row_index + 1
    end_row = configured_range.end_row_index
    return f"'{escaped_title}'!{start_column}{start_row}:{end_column}{end_row}"


def _column_label(column_index: int) -> str:
    value = column_index + 1
    label = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


__all__ = [
    "ConfiguredRange",
    "ConfiguredRangeCoverageProof",
    "ConfiguredReadContractError",
    "ConfiguredReadPlan",
    "ConfiguredReadResult",
    "ConfiguredSheet",
    "CoveredRange",
    "MAPPER_VERSION",
    "REQUIRED_GOOGLE_RESPONSE_FIELDS",
]
