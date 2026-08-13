from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path

import pytest
from requests import Response
from requests.adapters import HTTPAdapter

import marketing_knowledge_agent.google_sheets_runtime_config as config_module
import marketing_knowledge_agent.google_sheets_transport as transport_module
from marketing_knowledge_agent.google_sheets_read_contracts import (
    ConfiguredReadResult,
    REQUIRED_GOOGLE_RESPONSE_FIELDS,
)
from marketing_knowledge_agent.google_sheets_runtime_config import (
    production_google_sheets_runtime_config,
)
from marketing_knowledge_agent.google_sheets_transport import (
    GoogleSheetsTransportError,
)


SECRET = "never-expose-provider-token-or-session"
EXPECTED_FIELDS_SELECTOR = (
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


class FakeClock:
    def monotonic(self) -> float:
        return 0.0

    def utcnow(self) -> datetime:
        return datetime(2026, 8, 13, tzinfo=timezone.utc)

    def sleep(self, seconds: float) -> None:
        pass


class FakeCredentials:
    def __init__(self, *, force_refresh=False) -> None:
        self.force_refresh = force_refresh
        self.refresh_calls = 0

    def before_request(self, auth_request, method, url, headers) -> None:
        if self.force_refresh:
            auth_request(method="POST", url="https://oauth.example/token", body=SECRET)
        headers["authorization"] = f"Bearer {SECRET}"

    def refresh(self, request) -> None:
        self.refresh_calls += 1
        raise RuntimeError(SECRET)


class FakeProvider:
    def __init__(self, credentials=None) -> None:
        self.credentials = credentials or FakeCredentials()
        self.scopes = []

    def get_credentials(self, *, scopes):
        self.scopes.append(scopes)
        return self.credentials

    def __repr__(self) -> str:
        return f"FakeProvider({SECRET})"


class OneResponseAdapter(HTTPAdapter):
    def __init__(self, response=None, *, retries=0, error=None) -> None:
        super().__init__(max_retries=retries)
        self.response = response
        self.error = error
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        self.response.request = request
        return self.response


def _response(payload, status=200):
    response = Response()
    response.status_code = status
    response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response.headers["content-type"] = "application/json"
    return response


def _minimal_production_payload():
    config = production_google_sheets_runtime_config()
    ranges = {value.sheet_id: value for value in config.read_plan.ranges}
    sheets = []
    for sheet in config.read_plan.sheets:
        selected = ranges[sheet.sheet_id]
        properties = {
            "sheetId": sheet.sheet_id,
            "title": sheet.title,
            "gridProperties": {
                "rowCount": sheet.row_count,
                "columnCount": sheet.column_count,
            },
        }
        if sheet.hidden:
            properties["hidden"] = True
        sheets.append(
            {
                "properties": properties,
                "data": [
                    {
                        "startRow": selected.start_row_index,
                        "startColumn": selected.start_column_index,
                        "rowData": [],
                    }
                ],
                "merges": [],
            }
        )
    return {"spreadsheetId": config.read_plan.spreadsheet_id, "sheets": sheets}


def _build(provider, adapter):
    clock = FakeClock()
    return transport_module._create_google_sheets_transport_for_test(
        provider,
        adapter=adapter,
        clock=clock,
        sleeper=clock.sleep,
    )


def test_zero_argument_runtime_config_binds_exact_frozen_selection_and_policy():
    assert inspect.signature(production_google_sheets_runtime_config).parameters == {}

    config = production_google_sheets_runtime_config()
    policy = config.transport_policy
    assert config.read_plan.config_version == "s1-wp1-prod-read-selection-v1"
    assert config.read_plan.configuration_identity == (
        "sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e"
    )
    assert config.expected_selection_identity == config.read_plan.configuration_identity
    assert config.read_plan.spreadsheet_id == (
        "15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM"
    )
    assert config.read_plan.method == "spreadsheets.get"
    assert config.read_plan.include_grid_data is True
    assert config.read_plan.request_ranges == (
        "'商家/夥伴案例資料庫'!A6:L1018",
        "'「不可公開」客戶名單'!A4:H994",
        "'「可公開」對外數據'!A6:M999",
        "'待確認數據'!A3:D999",
        "'handle 比對'!A1:D998",
    )
    assert [
        (sheet.sheet_id, sheet.title, sheet.hidden, sheet.row_count, sheet.column_count)
        for sheet in config.read_plan.sheets
    ] == [
        (0, "商家/夥伴案例資料庫", False, 1018, 35),
        (1456785208, "「不可公開」客戶名單", False, 994, 28),
        (918878896, "「可公開」對外數據", False, 999, 30),
        (956677822, "待確認數據", True, 999, 26),
        (737692182, "handle 比對", True, 998, 26),
    ]
    assert policy.endpoint == (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        "15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM"
    )
    assert policy.http_method == "GET"
    assert policy.oauth_scopes == (
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    )
    assert policy.timeout.connect_seconds == 5.0
    assert policy.timeout.read_seconds == 30.0
    assert policy.timeout.overall_deadline_seconds == 90.0
    assert policy.retry.retry_statuses == (408, 429, 500, 502, 503, 504)
    assert policy.retry.maximum_attempts == 2
    assert policy.retry.fallback_delay_seconds == 1.0
    assert policy.retry.retry_after_enabled is True
    assert policy.retry.jitter_enabled is False
    assert policy.retry.redirects_allowed is False
    assert config.transport_policy_version == "s1-wp1-google-sheets-transport-v1"
    assert config.transport_policy_identity.startswith("sha256:")
    assert config.transport_policy_identity != config.expected_selection_identity


def test_exact_rest_fields_selector_independently_covers_row_data_values():
    config = production_google_sheets_runtime_config()

    assert config.transport_policy.fields_selector == EXPECTED_FIELDS_SELECTOR
    assert "sheets.data(startRow,startColumn,rowData.values(" in EXPECTED_FIELDS_SELECTOR
    for required in (
        "formattedValue",
        "effectiveValue(stringValue,numberValue,boolValue,formulaValue,errorValue(type,message))",
        "userEnteredValue(stringValue,numberValue,boolValue,formulaValue,errorValue(type,message))",
        "hyperlink",
        "textFormatRuns(startIndex,format.link(uri))",
        "dataValidation(condition(type,values(relativeDate,userEnteredValue)),inputMessage,strict,showCustomUi)",
    ):
        assert required in EXPECTED_FIELDS_SELECTOR
    assert config.read_plan.fields == REQUIRED_GOOGLE_RESPONSE_FIELDS
    assert config.read_plan.include_grid_data is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda policy: replace(policy, request_ranges=tuple(reversed(policy.request_ranges))),
        lambda policy: replace(policy, fields_selector=policy.fields_selector + ",extra"),
        lambda policy: replace(policy, endpoint="https://sheets.googleapis.com/wrong"),
        lambda policy: replace(policy, http_method="POST"),
        lambda policy: replace(policy, semantic_method="values.get"),
        lambda policy: replace(policy, include_grid_data=False),
        lambda policy: replace(policy, oauth_scopes=("drive.readonly",)),
        lambda policy: replace(
            policy,
            timeout=replace(policy.timeout, connect_seconds=6.0),
        ),
        lambda policy: replace(
            policy,
            retry=replace(policy.retry, retry_statuses=(429,)),
        ),
        lambda policy: replace(
            policy,
            retry=replace(policy.retry, maximum_attempts=3),
        ),
        lambda policy: replace(
            policy,
            retry=replace(policy.retry, retry_after_enabled=False),
        ),
        lambda policy: replace(
            policy,
            retry=replace(policy.retry, redirects_allowed=True),
        ),
    ],
)
def test_transport_policy_mutations_change_identity_expectation(mutate):
    policy = production_google_sheets_runtime_config().transport_policy
    mutated = mutate(policy)

    assert mutated.transport_policy_identity != policy.transport_policy_identity


def test_caller_cannot_override_request_or_runtime_authority():
    assert set(inspect.signature(transport_module.GoogleSheetsTransport.read).parameters) == {
        "self"
    }
    assert set(
        inspect.signature(transport_module.create_google_sheets_transport).parameters
    ) == {"credential_provider"}
    forbidden = {
        "url",
        "endpoint",
        "method",
        "spreadsheet_id",
        "ranges",
        "fields",
        "retry",
        "config",
        "config_path",
    }
    assert not forbidden & set(
        inspect.signature(transport_module.create_google_sheets_transport).parameters
    )
    with pytest.raises(
        TypeError, match="GOOGLE_SHEETS_TRANSPORT_CONSTRUCTION_FORBIDDEN"
    ):
        transport_module.GoogleSheetsTransport(
            config="caller-config",
            session="caller-session",
            clock="caller-clock",
            sleeper="caller-sleeper",
        )


def test_credential_scope_session_retry_and_adapter_boundaries_are_mechanical():
    credentials = FakeCredentials()
    provider = FakeProvider(credentials)
    adapter = OneResponseAdapter(_response(_minimal_production_payload()))

    transport = _build(provider, adapter)
    result = transport.read()

    assert isinstance(result, ConfiguredReadResult)
    assert provider.scopes == [
        ("https://www.googleapis.com/auth/spreadsheets.readonly",)
    ]
    assert transport._session._refresh_status_codes == ()
    assert transport._session._max_refresh_attempts == 0
    assert transport._session.get_adapter(
        production_google_sheets_runtime_config().transport_policy.endpoint
    ).max_retries.total == 0
    assert credentials.refresh_calls == 0
    assert not hasattr(transport, "credentials")
    assert not hasattr(transport, "credential_provider")
    assert SECRET not in repr(transport)
    assert SECRET not in repr(result)
    assert SECRET not in repr(result.snapshot)


def test_nonzero_adapter_retry_is_rejected_before_credentials_are_requested():
    provider = FakeProvider()
    adapter = OneResponseAdapter(_response({}), retries=1)

    with pytest.raises(GoogleSheetsTransportError) as caught:
        _build(provider, adapter)

    assert caught.value.code == "GOOGLE_SHEETS_ADAPTER_RETRIES_NOT_ZERO"
    assert provider.scopes == []


def test_token_refresh_path_is_blocked_without_auth_or_data_network():
    credentials = FakeCredentials(force_refresh=True)
    provider = FakeProvider(credentials)
    adapter = OneResponseAdapter(_response(_minimal_production_payload()))
    transport = _build(provider, adapter)

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_CREDENTIAL_REFRESH_FORBIDDEN"
    assert adapter.requests == []
    assert credentials.refresh_calls == 0
    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)
    assert caught.value.__context__ is None


def test_provider_session_and_error_secrets_are_sanitized(caplog):
    class FailingProvider:
        def get_credentials(self, *, scopes):
            raise RuntimeError(SECRET)

        def __repr__(self):
            return SECRET

    adapter = OneResponseAdapter(_response({}))
    with pytest.raises(GoogleSheetsTransportError) as provider_error:
        _build(FailingProvider(), adapter)
    assert provider_error.value.code == "GOOGLE_SHEETS_CREDENTIAL_PROVIDER_FAILED"
    assert SECRET not in str(provider_error.value)
    assert SECRET not in repr(provider_error.value)
    assert provider_error.value.__context__ is None

    provider = FakeProvider()
    transport = _build(provider, OneResponseAdapter(error=RuntimeError(SECRET)))
    with pytest.raises(GoogleSheetsTransportError) as session_error:
        transport.read()
    assert session_error.value.code == "GOOGLE_SHEETS_AUTH_OR_SESSION_FAILED"
    assert SECRET not in str(session_error.value)
    assert SECRET not in repr(session_error.value)
    assert session_error.value.__context__ is None
    assert caplog.records == []


def test_no_production_credential_discovery_or_forbidden_api_surface_in_source():
    config_source = inspect.getsource(config_module)
    transport_source = inspect.getsource(transport_module)
    combined = config_source + transport_source

    for forbidden in (
        "google.auth.default",
        "service_account",
        "impersonated_credentials",
        "google.oauth2",
        "googleapiclient",
        "batchUpdate",
        "drive.googleapis.com",
        "script.googleapis.com",
        "os.environ",
        "getenv(",
        "config_path",
    ):
        assert forbidden not in combined


def test_dependency_constraint_is_identical_in_both_authorized_manifests():
    root = Path(__file__).resolve().parents[2]
    constraint = "google-auth[requests]>=2.50,<2.51"
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    setup = (root / "setup.py").read_text(encoding="utf-8")

    assert pyproject.count(constraint) == 1
    assert setup.count(constraint) == 1
