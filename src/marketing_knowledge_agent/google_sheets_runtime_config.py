"""Frozen, source-bound runtime configuration for the S1-WP1 Sheets read."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Tuple

from .google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredReadPlan,
    ConfiguredSheet,
    REQUIRED_GOOGLE_RESPONSE_FIELDS,
)


_CONFIG_VERSION = "s1-wp1-prod-read-selection-v1"
_EXPECTED_SELECTION_IDENTITY = (
    "sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e"
)
_SPREADSHEET_ID = "15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM"
_SEMANTIC_METHOD = "spreadsheets.get"
_HTTP_METHOD = "GET"
_ENDPOINT = f"https://sheets.googleapis.com/v4/spreadsheets/{_SPREADSHEET_ID}"
_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_FIELDS_SELECTOR = (
    "spreadsheetId,"
    "sheets.properties(sheetId,title,hidden,gridProperties(rowCount,columnCount)),"
    "sheets.data(startRow,startColumn,rowData.values(formattedValue,"
    "effectiveValue(stringValue,numberValue,boolValue,formulaValue,errorValue(type,message)),"
    "userEnteredValue(stringValue,numberValue,boolValue,formulaValue,errorValue(type,message)),"
    "hyperlink,textFormatRuns(startIndex,format.link(uri)),"
    "dataValidation(condition(type,values(relativeDate,userEnteredValue)),"
    "inputMessage,strict,showCustomUi))),"
    "sheets.merges(sheetId,startRowIndex,endRowIndex,startColumnIndex,endColumnIndex)"
)
_REQUEST_RANGES = (
    "'商家/夥伴案例資料庫'!A6:L1018",
    "'「不可公開」客戶名單'!A4:H994",
    "'「可公開」對外數據'!A6:M999",
    "'待確認數據'!A3:D999",
    "'handle 比對'!A1:D998",
)
_TRANSPORT_POLICY_VERSION = "s1-wp1-google-sheets-transport-v1"
_RETRY_STATUSES = (408, 429, 500, 502, 503, 504)


class GoogleSheetsRuntimeConfigError(ValueError):
    """Stable failure for a source configuration that no longer matches freeze."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


@dataclass(frozen=True)
class TimeoutPolicy:
    connect_seconds: float
    read_seconds: float
    overall_deadline_seconds: float


@dataclass(frozen=True)
class RetryPolicy:
    retry_statuses: Tuple[int, ...]
    maximum_attempts: int
    fallback_delay_seconds: float
    retry_after_enabled: bool
    jitter_enabled: bool
    redirects_allowed: bool


@dataclass(frozen=True)
class TransportPolicy:
    """Immutable policy whose identity is separate from WP0 selection identity."""

    transport_policy_version: str
    frozen_selection_identity: str
    request_ranges: Tuple[str, ...]
    fields_selector: str
    endpoint: str
    http_method: str
    semantic_method: str
    include_grid_data: bool
    oauth_scopes: Tuple[str, ...]
    timeout: TimeoutPolicy
    retry: RetryPolicy

    @property
    def transport_policy_identity(self) -> str:
        payload = {
            "endpoint": self.endpoint,
            "fields_selector": self.fields_selector,
            "frozen_selection_identity": self.frozen_selection_identity,
            "http_method": self.http_method,
            "include_grid_data": self.include_grid_data,
            "oauth_scopes": self.oauth_scopes,
            "request_ranges": self.request_ranges,
            "retry": {
                "fallback_delay_seconds": self.retry.fallback_delay_seconds,
                "jitter_enabled": self.retry.jitter_enabled,
                "maximum_attempts": self.retry.maximum_attempts,
                "redirects_allowed": self.retry.redirects_allowed,
                "retry_after_enabled": self.retry.retry_after_enabled,
                "retry_statuses": self.retry.retry_statuses,
            },
            "semantic_method": self.semantic_method,
            "timeout": {
                "connect_seconds": self.timeout.connect_seconds,
                "overall_deadline_seconds": self.timeout.overall_deadline_seconds,
                "read_seconds": self.timeout.read_seconds,
            },
            "transport_policy_version": self.transport_policy_version,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


@dataclass(frozen=True)
class GoogleSheetsRuntimeConfig:
    """Complete production selection and transport policy, without credentials."""

    read_plan: ConfiguredReadPlan
    expected_selection_identity: str
    transport_policy: TransportPolicy

    @property
    def transport_policy_version(self) -> str:
        return self.transport_policy.transport_policy_version

    @property
    def transport_policy_identity(self) -> str:
        return self.transport_policy.transport_policy_identity

    def __repr__(self) -> str:
        return (
            "GoogleSheetsRuntimeConfig("
            f"config_version={self.read_plan.config_version!r}, "
            f"selection_identity={self.expected_selection_identity!r}, "
            f"transport_policy_version={self.transport_policy_version!r}, "
            f"transport_policy_identity={self.transport_policy_identity!r})"
        )


def production_google_sheets_runtime_config() -> GoogleSheetsRuntimeConfig:
    """Return the sole S1-WP1 production configuration from source constants."""

    read_plan = ConfiguredReadPlan(
        spreadsheet_id=_SPREADSHEET_ID,
        config_version=_CONFIG_VERSION,
        sheets=(
            ConfiguredSheet(0, "商家/夥伴案例資料庫", False, 1018, 35),
            ConfiguredSheet(1456785208, "「不可公開」客戶名單", False, 994, 28),
            ConfiguredSheet(918878896, "「可公開」對外數據", False, 999, 30),
            ConfiguredSheet(956677822, "待確認數據", True, 999, 26),
            ConfiguredSheet(737692182, "handle 比對", True, 998, 26),
        ),
        ranges=(
            ConfiguredRange("merchant_case", 0, 5, 1018, 0, 12),
            ConfiguredRange("restricted_customer", 1456785208, 3, 994, 0, 8),
            ConfiguredRange("public_metric", 918878896, 5, 999, 0, 13),
            ConfiguredRange("pending_metric", 956677822, 2, 999, 0, 4),
            ConfiguredRange("handle_mapping", 737692182, 0, 998, 0, 4),
        ),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
        method=_SEMANTIC_METHOD,
        include_grid_data=True,
    )
    if read_plan.configuration_identity != _EXPECTED_SELECTION_IDENTITY:
        _fail("RUNTIME_SELECTION_IDENTITY_MISMATCH")
    if read_plan.request_ranges != _REQUEST_RANGES:
        _fail("RUNTIME_REQUEST_RANGES_MISMATCH")

    transport_policy = TransportPolicy(
        transport_policy_version=_TRANSPORT_POLICY_VERSION,
        frozen_selection_identity=_EXPECTED_SELECTION_IDENTITY,
        request_ranges=_REQUEST_RANGES,
        fields_selector=_FIELDS_SELECTOR,
        endpoint=_ENDPOINT,
        http_method=_HTTP_METHOD,
        semantic_method=_SEMANTIC_METHOD,
        include_grid_data=True,
        oauth_scopes=(_READONLY_SCOPE,),
        timeout=TimeoutPolicy(
            connect_seconds=5.0,
            read_seconds=30.0,
            overall_deadline_seconds=90.0,
        ),
        retry=RetryPolicy(
            retry_statuses=_RETRY_STATUSES,
            maximum_attempts=2,
            fallback_delay_seconds=1.0,
            retry_after_enabled=True,
            jitter_enabled=False,
            redirects_allowed=False,
        ),
    )
    _validate_transport_policy(transport_policy, read_plan)
    return GoogleSheetsRuntimeConfig(
        read_plan=read_plan,
        expected_selection_identity=_EXPECTED_SELECTION_IDENTITY,
        transport_policy=transport_policy,
    )


def _validate_transport_policy(
    policy: TransportPolicy, read_plan: ConfiguredReadPlan
) -> None:
    if policy.frozen_selection_identity != read_plan.configuration_identity:
        _fail("RUNTIME_TRANSPORT_SELECTION_BINDING_MISMATCH")
    if policy.request_ranges != read_plan.request_ranges:
        _fail("RUNTIME_TRANSPORT_RANGE_BINDING_MISMATCH")
    if policy.fields_selector != _FIELDS_SELECTOR:
        _fail("RUNTIME_FIELDS_SELECTOR_MISMATCH")
    if policy.endpoint != _ENDPOINT or policy.http_method != _HTTP_METHOD:
        _fail("RUNTIME_ENDPOINT_MISMATCH")
    if (
        policy.semantic_method != read_plan.method
        or policy.include_grid_data is not read_plan.include_grid_data
    ):
        _fail("RUNTIME_METHOD_BINDING_MISMATCH")
    if policy.oauth_scopes != (_READONLY_SCOPE,):
        _fail("RUNTIME_SCOPE_MISMATCH")
    if policy.timeout != TimeoutPolicy(5.0, 30.0, 90.0):
        _fail("RUNTIME_TIMEOUT_POLICY_MISMATCH")
    if policy.retry != RetryPolicy(
        _RETRY_STATUSES, 2, 1.0, True, False, False
    ):
        _fail("RUNTIME_RETRY_POLICY_MISMATCH")


def _fail(code: str) -> None:
    raise GoogleSheetsRuntimeConfigError(code) from None


__all__ = [
    "GoogleSheetsRuntimeConfig",
    "GoogleSheetsRuntimeConfigError",
    "RetryPolicy",
    "TimeoutPolicy",
    "TransportPolicy",
    "production_google_sheets_runtime_config",
]
