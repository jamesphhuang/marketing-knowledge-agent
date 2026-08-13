"""Offline Sprint 1 WP2 source-health envelope construction."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Callable, Dict, Optional, Set, Tuple

from .canonical_serialization import compute_source_fingerprint
from .google_sheets_dry_run_contracts import (
    CoverageProvenBatchContext,
    RunMode,
    SafeStructuralCounts,
    SourceHealthDisposition,
    _canonical_deferred_check_codes,
    _canonical_structural_reason_codes,
    _context_binding_is_valid,
    _context_envelope,
    _context_result,
    _create_coverage_proven_batch_context,
)
from .google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredRangeCoverageProof,
    ConfiguredReadResult,
    ConfiguredSheet,
    CoveredRange,
)
from .google_sheets_runtime_config import production_google_sheets_runtime_config
from .sheets_contracts import CellData, SheetSnapshot, SpreadsheetSnapshot


SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION = "s1-wp2-source-health-envelope-v1"
SOURCE_HEALTH_RULES_VERSION = "s1-wp2-source-health-rules-v1"
SNAPSHOT_SCHEMA_VERSION = "google-sheets-snapshot-schema-v1"
FINGERPRINT_SEMANTICS_VERSION = "canonical-source-snapshot-v1"
_TARGET_IDENTITY_DOMAIN = b"google-sheets-target-identity:v1\x00"
_COVERAGE_IDENTITY_DOMAIN = b"configured-range-coverage:v1\x00"

_HEADER_BINDINGS = {
    "merchant_case": (
        "採訪年份",
        "狀態",
        "商家 / 夥伴名稱",
        "Handle",
        "Sales Category LV1",
        "Sales Category LV2",
        "內容相關標籤",
        "文章",
        "影片",
        "Podcast",
        "新聞",
        "備註",
    ),
    "restricted_customer": (
        "更新年份",
        "客戶品牌",
        "網站",
        "Sales Category LV1",
        "是否有簽保密 NDA",
        "NDA是否已上傳Salesforce",
        "店家狀況（例如：店家對中資事件敏感...）",
        "填表人（部門/名字）",
    ),
    "public_metric": (
        "類型",
        "指標",
        "論述",
        "備註",
        "更新時間",
        "參考新聞連結",
        "新聞稿",
        "自媒體",
        "Saleskits",
        "口頭說明",
        "演講簡報",
        "官網/ 招募網站",
        "廣告",
    ),
    "handle_mapping": (
        "Handle",
        "Name (with Link)",
        "Lv1 Sales Category",
        "Lv2 Sales Category 1st",
    ),
}

_HEADER_FAILURE_CODES = {
    "merchant_case": "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
    "restricted_customer": "SOURCE_HEALTH_RESTRICTED_CUSTOMER_HEADER_MISMATCH",
    "public_metric": "SOURCE_HEALTH_PUBLIC_METRIC_HEADER_MISMATCH",
    "handle_mapping": "SOURCE_HEALTH_HANDLE_MAPPING_HEADER_MISMATCH",
}

_DEFERRED_CHECK_CODES = tuple(
    sorted(
        (
            "BRAND_ID_INITIAL_REVIEW_DEFERRED",
            "BRAND_ID_MAPPING_AUTHORITY_DEFERRED",
            "MERCHANT_BRD_ASSIGNMENT_DEFERRED",
            "MERCHANT_ID_REVIEW_STATUS_DEFERRED",
            "MERCHANT_MREC_ASSIGNMENT_DEFERRED",
            "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED",
        )
    )
)
_SENSITIVE_ROW_CAPACITIES = {
    "merchant_case": 1012,
    "restricted_customer": 990,
    "public_metric": 993,
    "pending_metric": 997,
    "handle_mapping": 997,
}


class SourceHealthError(ValueError):
    """Stable payload-free source-health failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class SensitiveOccupiedRowCounts:
    """Non-serializable, in-memory-only source-category row facts."""

    __slots__ = (
        "_merchant_case",
        "_restricted_customer",
        "_public_metric",
        "_pending_metric",
        "_handle_mapping",
    )

    def __init__(
        self,
        *,
        merchant_case: int,
        restricted_customer: int,
        public_metric: int,
        pending_metric: int,
        handle_mapping: int,
    ) -> None:
        values = (
            merchant_case,
            restricted_customer,
            public_metric,
            pending_metric,
            handle_mapping,
        )
        if any(type(value) is not int or value < 0 for value in values):
            _fail("SOURCE_HEALTH_OCCUPIED_ROW_COUNT_INVALID")
        if any(
            value > _SENSITIVE_ROW_CAPACITIES[name]
            for name, value in zip(_SENSITIVE_ROW_CAPACITIES, values)
        ):
            _fail("SOURCE_HEALTH_OCCUPIED_ROW_COUNT_OUT_OF_BOUNDS")
        object.__setattr__(self, "_merchant_case", merchant_case)
        object.__setattr__(self, "_restricted_customer", restricted_customer)
        object.__setattr__(self, "_public_metric", public_metric)
        object.__setattr__(self, "_pending_metric", pending_metric)
        object.__setattr__(self, "_handle_mapping", handle_mapping)

    @property
    def merchant_case(self) -> int:
        return self._merchant_case

    @property
    def restricted_customer(self) -> int:
        return self._restricted_customer

    @property
    def public_metric(self) -> int:
        return self._public_metric

    @property
    def pending_metric(self) -> int:
        return self._pending_metric

    @property
    def handle_mapping(self) -> int:
        return self._handle_mapping

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SENSITIVE_OCCUPIED_ROW_COUNTS_IMMUTABLE")

    def __repr__(self) -> str:
        return "SensitiveOccupiedRowCounts(<redacted>)"

    def __reduce__(self) -> object:
        raise TypeError("SENSITIVE_OCCUPIED_ROW_COUNTS_PICKLE_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("SENSITIVE_OCCUPIED_ROW_COUNTS_PICKLE_FORBIDDEN")


def _coerce_safe_counts(value: object) -> SafeStructuralCounts:
    if type(value) is SafeStructuralCounts:
        return value
    if not isinstance(value, Mapping):
        _fail("SOURCE_HEALTH_SAFE_COUNTS_INVALID")
    safe_counts = None
    try:
        copied = dict(value)
        expected = set(SafeStructuralCounts.__dataclass_fields__)
        if set(copied) == expected:
            safe_counts = SafeStructuralCounts(**copied)
    except Exception:
        pass
    if safe_counts is None:
        _fail("SOURCE_HEALTH_SAFE_COUNTS_INVALID")
    return safe_counts


def _coerce_sensitive_counts(value: object) -> SensitiveOccupiedRowCounts:
    if type(value) is SensitiveOccupiedRowCounts:
        return value
    if not isinstance(value, Mapping):
        _fail("SOURCE_HEALTH_SENSITIVE_COUNTS_INVALID")
    sensitive_counts = None
    try:
        copied = dict(value)
        if set(copied) == set(_SENSITIVE_ROW_CAPACITIES):
            sensitive_counts = SensitiveOccupiedRowCounts(**copied)
    except Exception:
        pass
    if sensitive_counts is None:
        _fail("SOURCE_HEALTH_SENSITIVE_COUNTS_INVALID")
    return sensitive_counts


def _sensitive_count_values(
    value: SensitiveOccupiedRowCounts,
) -> Tuple[int, int, int, int, int]:
    if type(value) is not SensitiveOccupiedRowCounts:
        _fail("SOURCE_HEALTH_SENSITIVE_COUNTS_INVALID")
    return (
        value.merchant_case,
        value.restricted_customer,
        value.public_metric,
        value.pending_metric,
        value.handle_mapping,
    )


@dataclass(frozen=True)
class SourceHealthEnvelope:
    """Internal immutable WP2 envelope; never a durable artifact."""

    schema_version: str
    run_mode: RunMode
    correlation_id: str
    target_identity_hash: str
    configuration_identity: str
    config_version: str
    coverage_identity: str
    mapper_version: str
    snapshot_schema_version: str
    fingerprint_semantics_version: str
    source_health_rules_version: str
    source_fingerprint: str
    safe_counts: SafeStructuralCounts
    sensitive_occupied_row_counts: SensitiveOccupiedRowCounts = field(repr=False)
    structural_reason_codes: Tuple[str, ...]
    deferred_check_codes: Tuple[str, ...]
    disposition: SourceHealthDisposition
    _result_object_identity: int = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        safe_counts = _coerce_safe_counts(self.safe_counts)
        sensitive_counts = _coerce_sensitive_counts(
            self.sensitive_occupied_row_counts
        )
        structural_codes = _canonical_structural_reason_codes(
            self.structural_reason_codes
        )
        deferred_codes = _canonical_deferred_check_codes(self.deferred_check_codes)
        if safe_counts.structural_issue_count != len(structural_codes):
            _fail("SOURCE_HEALTH_STRUCTURAL_ISSUE_COUNT_MISMATCH")
        if type(self.run_mode) is not RunMode:
            _fail("SOURCE_HEALTH_RUN_MODE_INVALID")
        if type(self.disposition) is not SourceHealthDisposition:
            _fail("SOURCE_HEALTH_DISPOSITION_INVALID")
        if type(self._result_object_identity) is not int:
            _fail("SOURCE_HEALTH_RESULT_IDENTITY_INVALID")
        object.__setattr__(self, "safe_counts", safe_counts)
        object.__setattr__(self, "sensitive_occupied_row_counts", sensitive_counts)
        object.__setattr__(self, "structural_reason_codes", structural_codes)
        object.__setattr__(self, "deferred_check_codes", deferred_codes)

    def __repr__(self) -> str:
        return "SourceHealthEnvelope(<sensitive>)"


def build_first_live_coverage_proven_batch_context(
    configured_read_result: ConfiguredReadResult,
    *,
    _correlation_id_factory: Optional[Callable[[], object]] = None,
) -> CoverageProvenBatchContext:
    """Build a trusted first-live context with mandatory human review semantics."""

    return _build_coverage_proven_batch_context(
        configured_read_result,
        RunMode.FIRST_LIVE,
        _correlation_id_factory,
    )


def build_synthetic_coverage_proven_batch_context(
    configured_read_result: ConfiguredReadResult,
    *,
    _correlation_id_factory: Optional[Callable[[], object]] = None,
) -> CoverageProvenBatchContext:
    """Build an offline synthetic context that cannot form first-live evidence."""

    return _build_coverage_proven_batch_context(
        configured_read_result,
        RunMode.SYNTHETIC,
        _correlation_id_factory,
    )


def _build_coverage_proven_batch_context(
    configured_read_result: ConfiguredReadResult,
    run_mode: RunMode,
    correlation_id_factory: Optional[Callable[[], object]],
) -> CoverageProvenBatchContext:
    """Validate one frozen configured result and build its in-memory WP2 context."""

    plan = _validate_configured_result(configured_read_result)
    correlation_id = _new_correlation_id(correlation_id_factory)

    snapshot = configured_read_result.snapshot
    proof = configured_read_result.coverage_proof
    reason_codes, valid_headers, valid_positionals, observed_sheets = (
        _structural_reason_codes(snapshot, plan.sheets, plan.ranges)
    )
    reason_codes_tuple = tuple(sorted(reason_codes))
    safe_counts = SafeStructuralCounts(
        configured_range_count=len(plan.ranges),
        covered_range_count=proof.observed_range_count,
        configured_sheet_count=len(plan.sheets),
        observed_sheet_count=len(snapshot.sheets),
        critical_sheet_expected_count=len(plan.sheets),
        critical_sheet_observed_count=observed_sheets,
        header_binding_expected_count=len(_HEADER_BINDINGS),
        header_binding_valid_count=valid_headers,
        positional_binding_expected_count=1,
        positional_binding_valid_count=valid_positionals,
        structural_issue_count=len(reason_codes_tuple),
    )
    sensitive_counts = _occupied_row_counts(snapshot, plan.ranges)

    source_fingerprint = None
    try:
        source_fingerprint = compute_source_fingerprint(snapshot)
    except Exception:
        pass
    if source_fingerprint is None:
        _fail("SOURCE_HEALTH_FINGERPRINT_FAILED")

    disposition = _disposition(run_mode, reason_codes_tuple)
    envelope = SourceHealthEnvelope(
        schema_version=SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION,
        run_mode=run_mode,
        correlation_id=correlation_id,
        target_identity_hash=_target_identity_hash(snapshot.spreadsheet_id),
        configuration_identity=configured_read_result.configuration_identity,
        config_version=proof.config_version,
        coverage_identity=_coverage_identity(proof),
        mapper_version=proof.mapper_version,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        fingerprint_semantics_version=FINGERPRINT_SEMANTICS_VERSION,
        source_health_rules_version=SOURCE_HEALTH_RULES_VERSION,
        source_fingerprint=source_fingerprint,
        safe_counts=safe_counts,
        sensitive_occupied_row_counts=sensitive_counts,
        structural_reason_codes=reason_codes_tuple,
        deferred_check_codes=_DEFERRED_CHECK_CODES,
        disposition=disposition,
        _result_object_identity=id(configured_read_result),
    )
    return _create_coverage_proven_batch_context(configured_read_result, envelope)


def _validate_configured_result(configured_read_result: object):
    if type(configured_read_result) is not ConfiguredReadResult:
        _fail("SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED")
    try:
        snapshot = configured_read_result.snapshot
        proof = configured_read_result.coverage_proof
        configuration_identity = configured_read_result.configuration_identity
    except (AttributeError, TypeError):
        _fail("SOURCE_HEALTH_CONFIGURED_READ_RESULT_INVALID")
    if type(snapshot) is not SpreadsheetSnapshot:
        _fail("SOURCE_HEALTH_SNAPSHOT_INVALID")
    if type(proof) is not ConfiguredRangeCoverageProof:
        _fail("SOURCE_HEALTH_COVERAGE_PROOF_INVALID")
    if (
        configuration_identity != proof.configuration_identity
        or configured_read_result.configuration_identity != configuration_identity
    ):
        _fail("SOURCE_HEALTH_RESULT_PROOF_BINDING_MISMATCH")

    runtime_config = production_google_sheets_runtime_config()
    plan = runtime_config.read_plan
    if (
        configuration_identity != runtime_config.expected_selection_identity
        or configuration_identity != plan.configuration_identity
        or proof.config_version != plan.config_version
        or proof.mapper_version != plan.mapper_version
        or proof.method != plan.method
        or proof.include_grid_data is not plan.include_grid_data
    ):
        _fail("SOURCE_HEALTH_FROZEN_SELECTION_MISMATCH")
    if snapshot.spreadsheet_id != plan.spreadsheet_id:
        _fail("SOURCE_HEALTH_TARGET_MISMATCH")
    if (
        proof.expected_range_count != len(plan.ranges)
        or proof.observed_range_count != len(plan.ranges)
        or proof.observed_range_count != proof.expected_range_count
    ):
        _fail("SOURCE_HEALTH_COVERAGE_COUNT_MISMATCH")
    expected_coverage = tuple(
        _covered_range(value) for value in sorted(plan.ranges, key=lambda item: item.range_id)
    )
    if proof.covered_ranges != expected_coverage:
        _fail("SOURCE_HEALTH_COVERAGE_RANGES_MISMATCH")
    _coverage_identity(proof)
    return plan


def _structural_reason_codes(
    snapshot: SpreadsheetSnapshot,
    configured_sheets: Tuple[ConfiguredSheet, ...],
    configured_ranges: Tuple[ConfiguredRange, ...],
) -> Tuple[Set[str], int, int, int]:
    reasons: Set[str] = set()
    sheets_by_id = {sheet.sheet_id: sheet for sheet in snapshot.sheets}
    expected_sheets = {sheet.sheet_id: sheet for sheet in configured_sheets}
    observed_sheets = 0
    for sheet_id, expected in expected_sheets.items():
        observed = sheets_by_id.get(sheet_id)
        if observed is not None and _sheet_profile_matches(observed, expected):
            observed_sheets += 1
        else:
            reasons.add("SOURCE_HEALTH_CRITICAL_SHEET_PROFILE_MISMATCH")
    if set(sheets_by_id) != set(expected_sheets):
        reasons.add("SOURCE_HEALTH_CRITICAL_SHEET_SET_MISMATCH")

    ranges_by_id = {value.range_id: value for value in configured_ranges}
    valid_headers = 0
    for range_id, expected_headers in _HEADER_BINDINGS.items():
        configured_range = ranges_by_id.get(range_id)
        sheet = (
            sheets_by_id.get(configured_range.sheet_id)
            if configured_range is not None
            else None
        )
        if (
            configured_range is not None
            and sheet is not None
            and _header_matches(sheet, configured_range, expected_headers)
        ):
            valid_headers += 1
        else:
            reasons.add(_HEADER_FAILURE_CODES[range_id])

    pending = ranges_by_id.get("pending_metric")
    positional_valid = int(
        pending is not None
        and pending.sheet_id == 956677822
        and (
            pending.start_row_index,
            pending.end_row_index,
            pending.start_column_index,
            pending.end_column_index,
        )
        == (2, 999, 0, 4)
    )
    if not positional_valid:
        reasons.add("SOURCE_HEALTH_PENDING_POSITIONAL_BINDING_MISMATCH")
    return reasons, valid_headers, positional_valid, observed_sheets


def _header_matches(
    sheet: SheetSnapshot,
    configured_range: ConfiguredRange,
    expected_headers: Tuple[str, ...],
) -> bool:
    cells = {(cell.row_index, cell.column_index): cell for cell in sheet.cells}
    if len(expected_headers) != (
        configured_range.end_column_index - configured_range.start_column_index
    ):
        return False
    for offset, expected in enumerate(expected_headers):
        cell = cells.get(
            (
                configured_range.start_row_index,
                configured_range.start_column_index + offset,
            )
        )
        if cell is None or cell.formatted_value != expected:
            return False
        if (
            cell.user_entered_value is not None
            and cell.user_entered_value.formula_value is not None
        ):
            return False
    return True


def _sheet_profile_matches(observed: SheetSnapshot, expected: ConfiguredSheet) -> bool:
    return (
        observed.sheet_id == expected.sheet_id
        and observed.title == expected.title
        and observed.hidden is expected.hidden
        and observed.row_count == expected.row_count
        and observed.column_count == expected.column_count
    )


def _occupied_row_counts(
    snapshot: SpreadsheetSnapshot,
    configured_ranges: Tuple[ConfiguredRange, ...],
) -> SensitiveOccupiedRowCounts:
    sheets_by_id = {sheet.sheet_id: sheet for sheet in snapshot.sheets}
    counts: Dict[str, int] = {}
    header_range_ids = set(_HEADER_BINDINGS)
    for configured_range in configured_ranges:
        sheet = sheets_by_id.get(configured_range.sheet_id)
        first_data_row = configured_range.start_row_index + int(
            configured_range.range_id in header_range_ids
        )
        occupied_rows = {
            cell.row_index
            for cell in (() if sheet is None else sheet.cells)
            if first_data_row <= cell.row_index < configured_range.end_row_index
            and configured_range.start_column_index
            <= cell.column_index
            < configured_range.end_column_index
            and _cell_is_occupied(cell)
        }
        counts[configured_range.range_id] = len(occupied_rows)
    try:
        return SensitiveOccupiedRowCounts(
            merchant_case=counts["merchant_case"],
            restricted_customer=counts["restricted_customer"],
            public_metric=counts["public_metric"],
            pending_metric=counts["pending_metric"],
            handle_mapping=counts["handle_mapping"],
        )
    except KeyError:
        _fail("SOURCE_HEALTH_OCCUPIED_ROW_BINDING_INVALID")


def _cell_is_occupied(cell: CellData) -> bool:
    return any(
        (
            cell.formatted_value is not None,
            cell.effective_value is not None,
            cell.user_entered_value is not None,
            cell.hyperlink is not None,
            bool(cell.text_format_runs),
            cell.data_validation is not None,
        )
    )


def _covered_range(configured_range: ConfiguredRange) -> CoveredRange:
    return CoveredRange(
        range_id=configured_range.range_id,
        sheet_id=configured_range.sheet_id,
        start_row_index=configured_range.start_row_index,
        end_row_index=configured_range.end_row_index,
        start_column_index=configured_range.start_column_index,
        end_column_index=configured_range.end_column_index,
    )


def _target_identity_hash(spreadsheet_id: str) -> str:
    if type(spreadsheet_id) is not str or not spreadsheet_id:
        _fail("SOURCE_HEALTH_TARGET_INVALID")
    digest = hashlib.sha256(
        _TARGET_IDENTITY_DOMAIN + spreadsheet_id.encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


def _coverage_identity(proof: ConfiguredRangeCoverageProof) -> str:
    payload = {
        "configuration_identity": proof.configuration_identity,
        "config_version": proof.config_version,
        "mapper_version": proof.mapper_version,
        "method": proof.method,
        "include_grid_data": proof.include_grid_data,
        "expected_range_count": proof.expected_range_count,
        "observed_range_count": proof.observed_range_count,
        "covered_ranges": [
            {
                "range_id": value.range_id,
                "sheet_id": value.sheet_id,
                "start_row_index": value.start_row_index,
                "end_row_index": value.end_row_index,
                "start_column_index": value.start_column_index,
                "end_column_index": value.end_column_index,
            }
            for value in proof.covered_ranges
        ],
    }
    serialized = None
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        pass
    if serialized is None:
        _fail("SOURCE_HEALTH_COVERAGE_IDENTITY_FAILED")
    return "sha256:" + hashlib.sha256(
        _COVERAGE_IDENTITY_DOMAIN + serialized
    ).hexdigest()


def _new_correlation_id(factory: Optional[Callable[[], object]]) -> str:
    value = None
    try:
        generated = uuid.uuid4() if factory is None else factory()
        value = str(generated)
    except Exception:
        pass
    if value is None:
        _fail("SOURCE_HEALTH_CORRELATION_ID_INVALID")
    if _validated_correlation_id(value) is None:
        _fail("SOURCE_HEALTH_CORRELATION_ID_INVALID")
    return value


def _validated_correlation_id(value: object) -> Optional[str]:
    if (
        not isinstance(value, str)
        or len(value) != 36
        or value != value.strip()
        or value != value.lower()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    parsed = None
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        pass
    if parsed is None:
        return None
    if (
        parsed.version != 4
        or parsed.variant != uuid.RFC_4122
        or str(parsed) != value
    ):
        return None
    return value


def _disposition(
    run_mode: RunMode, structural_reason_codes: Tuple[str, ...]
) -> SourceHealthDisposition:
    if structural_reason_codes:
        return SourceHealthDisposition.STRUCTURAL_BLOCK
    if run_mode is RunMode.FIRST_LIVE:
        return SourceHealthDisposition.HUMAN_REVIEW_REQUIRED
    return SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE


def _context_coverage_binding_valid(value: object) -> bool:
    return _validated_context_envelope(value) is not None


def _validated_context_envelope(value: object) -> Optional[SourceHealthEnvelope]:
    """Return the exact envelope only when all result-derived facts still bind."""

    if type(value) is not CoverageProvenBatchContext:
        return None
    if not _context_binding_is_valid(value):
        return None
    try:
        result = _context_result(value)
        envelope = _context_envelope(value)
        if type(result) is not ConfiguredReadResult:
            return None
        if type(envelope) is not SourceHealthEnvelope:
            return None
        plan = _validate_configured_result(result)
        snapshot = result.snapshot
        proof = result.coverage_proof
        reasons, valid_headers, valid_positionals, observed_sheets = (
            _structural_reason_codes(snapshot, plan.sheets, plan.ranges)
        )
        reason_codes = tuple(sorted(reasons))
        safe_counts = SafeStructuralCounts(
            configured_range_count=len(plan.ranges),
            covered_range_count=proof.observed_range_count,
            configured_sheet_count=len(plan.sheets),
            observed_sheet_count=len(snapshot.sheets),
            critical_sheet_expected_count=len(plan.sheets),
            critical_sheet_observed_count=observed_sheets,
            header_binding_expected_count=len(_HEADER_BINDINGS),
            header_binding_valid_count=valid_headers,
            positional_binding_expected_count=1,
            positional_binding_valid_count=valid_positionals,
            structural_issue_count=len(reason_codes),
        )
        sensitive_counts = _occupied_row_counts(snapshot, plan.ranges)
        source_fingerprint = compute_source_fingerprint(snapshot)
        expected = (
            SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION,
            _target_identity_hash(snapshot.spreadsheet_id),
            result.configuration_identity,
            proof.config_version,
            _coverage_identity(proof),
            proof.mapper_version,
            SNAPSHOT_SCHEMA_VERSION,
            FINGERPRINT_SEMANTICS_VERSION,
            SOURCE_HEALTH_RULES_VERSION,
            source_fingerprint,
            safe_counts,
            _sensitive_count_values(sensitive_counts),
            reason_codes,
            _DEFERRED_CHECK_CODES,
            _disposition(envelope.run_mode, reason_codes),
            id(result),
        )
        actual = (
            envelope.schema_version,
            envelope.target_identity_hash,
            envelope.configuration_identity,
            envelope.config_version,
            envelope.coverage_identity,
            envelope.mapper_version,
            envelope.snapshot_schema_version,
            envelope.fingerprint_semantics_version,
            envelope.source_health_rules_version,
            envelope.source_fingerprint,
            envelope.safe_counts,
            _sensitive_count_values(envelope.sensitive_occupied_row_counts),
            envelope.structural_reason_codes,
            envelope.deferred_check_codes,
            envelope.disposition,
            envelope._result_object_identity,
        )
        if expected != actual:
            return None
        if _validated_correlation_id(envelope.correlation_id) is None:
            return None
        return envelope
    except Exception:
        return None


def _context_facts(
    context: CoverageProvenBatchContext,
) -> Tuple[str, str, str, str, str, str, str]:
    envelope = _context_envelope(context)
    return (
        envelope.target_identity_hash,
        envelope.configuration_identity,
        envelope.coverage_identity,
        envelope.mapper_version,
        envelope.snapshot_schema_version,
        envelope.fingerprint_semantics_version,
        envelope.source_fingerprint,
    )


def _fail(code: str) -> None:
    raise SourceHealthError(code) from None


__all__ = [
    "FINGERPRINT_SEMANTICS_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION",
    "SOURCE_HEALTH_RULES_VERSION",
    "SourceHealthError",
    "build_first_live_coverage_proven_batch_context",
    "build_synthetic_coverage_proven_batch_context",
]
