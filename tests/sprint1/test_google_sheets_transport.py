from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from urllib.parse import parse_qsl, urlsplit

import pytest
from requests import Response
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectTimeout, ReadTimeout

import marketing_knowledge_agent.google_sheets_transport as transport_module
from marketing_knowledge_agent.google_sheets_read_contracts import ConfiguredReadResult
from marketing_knowledge_agent.google_sheets_response_mapper import (
    GoogleSheetsResponseError,
)
from marketing_knowledge_agent.google_sheets_runtime_config import (
    production_google_sheets_runtime_config,
)
from marketing_knowledge_agent.google_sheets_transport import (
    GoogleSheetsTransportError,
)


SECRET = "credential-secret-sentinel"


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.wall_start = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
        self.sleeps = []

    def monotonic(self) -> float:
        return self.elapsed

    def utcnow(self) -> datetime:
        return self.wall_start + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


class FakeCredentials:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.before_request_calls = 0

    def before_request(self, auth_request, method, url, headers) -> None:
        self.before_request_calls += 1
        headers["authorization"] = f"Bearer {SECRET}"

    def refresh(self, request) -> None:
        self.refresh_calls += 1
        raise AssertionError(SECRET)


class FakeProvider:
    def __init__(self, credentials=None) -> None:
        self.credentials = credentials or FakeCredentials()
        self.scope_calls = []

    def get_credentials(self, *, scopes):
        self.scope_calls.append(scopes)
        return self.credentials

    def __repr__(self) -> str:
        return f"FakeProvider(secret={SECRET!r})"


class ScriptedAdapter(HTTPAdapter):
    def __init__(self, events) -> None:
        super().__init__(max_retries=0)
        self.events = list(events)
        self.requests = []
        self.send_kwargs = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.send_kwargs.append(kwargs)
        if not self.events:
            raise AssertionError("unexpected extra Sheets data request")
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        if callable(event):
            event = event(request)
        status, payload, headers = event
        response = Response()
        response.status_code = status
        response.headers.update(headers)
        response.request = request
        if isinstance(payload, bytes):
            response._content = payload
        else:
            response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response.headers["content-type"] = "application/json"
        return response


def _production_response() -> dict:
    config = production_google_sheets_runtime_config()
    blocks = {
        configured_range.sheet_id: {
            "startRow": configured_range.start_row_index,
            "startColumn": configured_range.start_column_index,
            "rowData": [],
        }
        for configured_range in config.read_plan.ranges
    }
    blocks[0]["rowData"] = [
        {
            "values": [
                {
                    "formattedValue": "linked formula",
                    "effectiveValue": {"stringValue": "effective"},
                    "userEnteredValue": {"formulaValue": "=A1"},
                    "hyperlink": "https://example.com/data-only-link",
                    "textFormatRuns": [
                        {
                            "startIndex": 0,
                            "format": {
                                "link": {"uri": "https://example.org/rich-data-only"}
                            },
                        }
                    ],
                    "dataValidation": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": "approved"}],
                        },
                        "inputMessage": "review",
                        "strict": True,
                        "showCustomUi": False,
                    },
                },
                {
                    "formattedValue": "0",
                    "effectiveValue": {"numberValue": 0},
                },
                {
                    "formattedValue": "FALSE",
                    "effectiveValue": {"boolValue": False},
                },
            ]
        }
    ]
    sheets = []
    for sheet in config.read_plan.sheets:
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
        merges = []
        if sheet.sheet_id == 0:
            merges = [
                {
                    "sheetId": 0,
                    "startRowIndex": 5,
                    "endRowIndex": 6,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                }
            ]
        sheets.append(
            {
                "properties": properties,
                "data": [blocks[sheet.sheet_id]],
                "merges": merges,
            }
        )
    return {
        "spreadsheetId": config.read_plan.spreadsheet_id,
        "sheets": sheets,
    }


def _transport(events, *, provider=None, clock=None):
    provider = provider or FakeProvider()
    clock = clock or FakeClock()
    adapter = ScriptedAdapter(events)
    transport = transport_module._create_google_sheets_transport_for_test(
        provider,
        adapter=adapter,
        clock=clock,
        sleeper=clock.sleep,
    )
    return transport, adapter, provider, clock


def test_exact_frozen_request_and_configured_result_success():
    transport, adapter, provider, _ = _transport([(200, _production_response(), {})])

    result = transport.read()

    assert isinstance(result, ConfiguredReadResult)
    assert result.configuration_identity == (
        "sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e"
    )
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    parsed = urlsplit(request.url)
    assert request.method == "GET"
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "sheets.googleapis.com",
        "/v4/spreadsheets/15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM",
    )
    config = production_google_sheets_runtime_config()
    assert parse_qsl(parsed.query) == [
        *(("ranges", value) for value in config.read_plan.request_ranges),
        ("fields", config.transport_policy.fields_selector),
        ("includeGridData", "true"),
    ]
    assert request.body is None
    assert adapter.send_kwargs[0]["timeout"] == (5.0, 30.0)
    assert adapter.send_kwargs[0]["verify"] is True
    assert provider.scope_calls == [
        ("https://www.googleapis.com/auth/spreadsheets.readonly",)
    ]


def test_success_maps_values_links_validation_and_merges_without_fetching_links():
    transport, adapter, _, _ = _transport([(200, _production_response(), {})])

    result = transport.read()

    merchant = result.snapshot.sheets[0]
    by_coordinate = {(cell.row_index, cell.column_index): cell for cell in merchant.cells}
    first = by_coordinate[(5, 0)]
    assert first.formatted_value == "linked formula"
    assert first.effective_value.string_value == "effective"
    assert first.user_entered_value.formula_value == "=A1"
    assert first.hyperlink == "https://example.com/data-only-link"
    assert first.text_format_runs[0].link.uri == "https://example.org/rich-data-only"
    assert first.data_validation.condition.condition_type == "ONE_OF_LIST"
    assert by_coordinate[(5, 1)].effective_value.number_value == 0
    assert by_coordinate[(5, 2)].effective_value.bool_value is False
    assert merchant.merges[0].end_column_index == 2
    assert len(adapter.requests) == 1
    assert "example.com" not in adapter.requests[0].url
    assert "example.org" not in adapter.requests[0].url


@pytest.mark.parametrize(
    "failure",
    [ConnectTimeout(SECRET), ReadTimeout(SECRET)],
)
def test_connect_and_read_timeout_retry_exactly_once(failure):
    transport, adapter, _, clock = _transport(
        [failure, (200, _production_response(), {})]
    )

    assert isinstance(transport.read(), ConfiguredReadResult)
    assert len(adapter.requests) == 2
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_approved_http_status_retries_once(status):
    transport, adapter, _, clock = _transport(
        [(status, {}, {}), (200, _production_response(), {})]
    )

    assert isinstance(transport.read(), ConfiguredReadResult)
    assert len(adapter.requests) == 2
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(
    ("header", "expected_delay"),
    [
        ("7", 7.0),
        ("not-a-date", 1.0),
    ],
)
def test_retry_after_delta_and_invalid_fallback(header, expected_delay):
    transport, _, _, clock = _transport(
        [(429, {}, {"Retry-After": header}), (200, _production_response(), {})]
    )

    transport.read()

    assert clock.sleeps == [expected_delay]


def test_retry_after_http_date_and_past_date_are_deterministic():
    future_clock = FakeClock()
    future = format_datetime(future_clock.utcnow() + timedelta(seconds=6), usegmt=True)
    transport, _, _, future_clock = _transport(
        [(503, {}, {"Retry-After": future}), (200, _production_response(), {})],
        clock=future_clock,
    )
    transport.read()
    assert future_clock.sleeps == [6.0]

    past_clock = FakeClock()
    past = format_datetime(past_clock.utcnow() - timedelta(seconds=10), usegmt=True)
    transport, _, _, past_clock = _transport(
        [(503, {}, {"Retry-After": past}), (200, _production_response(), {})],
        clock=past_clock,
    )
    transport.read()
    assert past_clock.sleeps == [0.0]


def test_retry_delay_exceeding_deadline_does_not_sleep_or_retry():
    transport, adapter, _, clock = _transport(
        [(429, {}, {"Retry-After": "91"}), (200, _production_response(), {})]
    )

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("digits", [400, 5000])
def test_huge_valid_retry_after_delta_never_falls_back_or_retries(digits):
    transport, adapter, _, clock = _transport(
        [
            (429, {}, {"Retry-After": "9" * digits}),
            (200, _production_response(), {}),
        ]
    )

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1
    assert clock.sleeps == []


def test_zero_retry_after_is_deterministic_and_retries_once():
    transport, adapter, _, clock = _transport(
        [(429, {}, {"Retry-After": "0"}), (200, _production_response(), {})]
    )

    assert isinstance(transport.read(), ConfiguredReadResult)
    assert len(adapter.requests) == 2
    assert clock.sleeps == [0.0]


def test_future_retry_after_date_beyond_deadline_does_not_retry():
    clock = FakeClock()
    future = format_datetime(clock.utcnow() + timedelta(seconds=120), usegmt=True)
    transport, adapter, _, clock = _transport(
        [(503, {}, {"Retry-After": future}), (200, _production_response(), {})],
        clock=clock,
    )

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1
    assert clock.sleeps == []


def test_response_arriving_at_logical_deadline_is_rejected():
    clock = FakeClock()

    def expire_deadline(request):
        clock.elapsed = 90.0
        return (200, _production_response(), {})

    transport, adapter, _, _ = _transport([expire_deadline], clock=clock)

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1


def test_json_decode_crossing_deadline_discards_payload(monkeypatch):
    clock = FakeClock()
    transport, adapter, _, _ = _transport(
        [(200, _production_response(), {})], clock=clock
    )
    original_json = Response.json

    def late_json(response, **kwargs):
        payload = original_json(response, **kwargs)
        clock.elapsed = 90.1
        return payload

    monkeypatch.setattr(Response, "json", late_json)

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1


@pytest.mark.parametrize("elapsed", [90.0, 90.1])
def test_mapper_reaching_or_crossing_deadline_discards_result(monkeypatch, elapsed):
    clock = FakeClock()
    transport, adapter, _, _ = _transport(
        [(200, _production_response(), {})], clock=clock
    )
    original_mapper = transport_module.map_google_sheets_response

    def late_mapper(payload, plan):
        result = original_mapper(payload, plan)
        clock.elapsed = elapsed
        return result

    monkeypatch.setattr(
        transport_module, "map_google_sheets_response", late_mapper
    )

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
    assert len(adapter.requests) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (201, "GOOGLE_SHEETS_NON_200_SUCCESS_REJECTED"),
        (204, "GOOGLE_SHEETS_NON_200_SUCCESS_REJECTED"),
        (301, "GOOGLE_SHEETS_REDIRECT_REJECTED"),
        (307, "GOOGLE_SHEETS_REDIRECT_REJECTED"),
        (400, "GOOGLE_SHEETS_BAD_REQUEST"),
        (401, "GOOGLE_SHEETS_UNAUTHORIZED"),
        (403, "GOOGLE_SHEETS_FORBIDDEN"),
        (404, "GOOGLE_SHEETS_TARGET_NOT_FOUND"),
        (418, "GOOGLE_SHEETS_HTTP_STATUS_UNAPPROVED"),
        (501, "GOOGLE_SHEETS_HTTP_STATUS_UNAPPROVED"),
    ],
)
def test_unapproved_statuses_fail_immediately(status, code):
    transport, adapter, provider, clock = _transport([(status, {}, {})])

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == code
    assert len(adapter.requests) == 1
    assert provider.credentials.refresh_calls == 0
    assert clock.sleeps == []


def test_redirect_location_is_never_followed():
    transport, adapter, _, _ = _transport(
        [(302, {}, {"Location": "https://evil.example/redirect"})]
    )

    with pytest.raises(GoogleSheetsTransportError, match="REDIRECT_REJECTED"):
        transport.read()

    assert len(adapter.requests) == 1
    assert adapter.requests[0].url.startswith("https://sheets.googleapis.com/")


def test_retry_budget_is_two_absolute_data_calls():
    provider = FakeProvider()
    transport, adapter, provider, _ = _transport(
        [(401, {}, {}), (200, _production_response(), {})], provider=provider
    )
    with pytest.raises(GoogleSheetsTransportError, match="UNAUTHORIZED"):
        transport.read()
    assert len(adapter.requests) == 1
    assert provider.credentials.refresh_calls == 0

    transport, adapter, _, _ = _transport([(503, {}, {}), (503, {}, {})])
    with pytest.raises(GoogleSheetsTransportError, match="RETRY_EXHAUSTED"):
        transport.read()
    assert len(adapter.requests) == 2


def test_malformed_json_empty_shape_and_non_mapping_fail_closed():
    transport, _, _, _ = _transport([(200, b"not-json", {})])
    with pytest.raises(GoogleSheetsTransportError) as malformed:
        transport.read()
    assert malformed.value.code == "GOOGLE_SHEETS_RESPONSE_JSON_INVALID"
    assert malformed.value.__context__ is None

    transport, _, _, _ = _transport([(200, [], {})])
    with pytest.raises(GoogleSheetsTransportError) as wrong_type:
        transport.read()
    assert wrong_type.value.code == "GOOGLE_SHEETS_RESPONSE_SHAPE_INVALID"

    transport, _, _, _ = _transport([(200, {}, {})])
    with pytest.raises(GoogleSheetsResponseError) as empty:
        transport.read()
    assert empty.value.code == "GOOGLE_RESPONSE_SHAPE_UNSUPPORTED"


def test_response_cleanup_failure_is_sanitized_and_has_no_partial_success():
    class CloseFailureResponse(Response):
        def close(self):
            raise RuntimeError(SECRET)

    response = CloseFailureResponse()
    response.status_code = 200
    response._content = json.dumps(_production_response()).encode("utf-8")
    adapter = ScriptedAdapter([])
    adapter.events = [lambda request: response]
    original_send = adapter.send

    def send_response(request, **kwargs):
        adapter.requests.append(request)
        response.request = request
        return response

    adapter.send = send_response
    provider = FakeProvider()
    clock = FakeClock()
    transport = transport_module._create_google_sheets_transport_for_test(
        provider,
        adapter=adapter,
        clock=clock,
        sleeper=clock.sleep,
    )

    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()

    assert caught.value.code == "GOOGLE_SHEETS_RESPONSE_CLOSE_FAILED"
    assert SECRET not in str(caught.value)
    assert caught.value.__context__ is None
    assert original_send is not None


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda payload: payload.__setitem__("spreadsheetId", "wrong"),
            "GOOGLE_RESPONSE_TARGET_MISMATCH",
        ),
        (
            lambda payload: payload["sheets"][0]["data"].clear(),
            "GOOGLE_RESPONSE_RANGE_MISSING",
        ),
        (
            lambda payload: payload["sheets"][0]["properties"][
                "gridProperties"
            ].__setitem__("rowCount", 1019),
            "GOOGLE_RESPONSE_GRID_BOUNDS_MISMATCH",
        ),
    ],
)
def test_target_range_and_grid_mismatch_fail_without_partial_success(mutate, code):
    payload = deepcopy(_production_response())
    mutate(payload)
    transport, _, _, _ = _transport([(200, payload, {})])

    with pytest.raises(GoogleSheetsResponseError) as caught:
        transport.read()

    assert caught.value.code == code


def test_mapper_failure_and_bare_snapshot_are_rejected(monkeypatch):
    def fail_mapper(payload, config):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(transport_module, "map_google_sheets_response", fail_mapper)
    transport, _, _, _ = _transport([(200, _production_response(), {})])
    with pytest.raises(GoogleSheetsTransportError) as caught:
        transport.read()
    assert caught.value.code == "GOOGLE_SHEETS_MAPPER_FAILED"
    assert SECRET not in str(caught.value)
    assert caught.value.__context__ is None

    monkeypatch.setattr(
        transport_module,
        "map_google_sheets_response",
        lambda payload, config: object(),
    )
    transport, _, _, _ = _transport([(200, _production_response(), {})])
    with pytest.raises(GoogleSheetsTransportError) as bare:
        transport.read()
    assert bare.value.code == "GOOGLE_SHEETS_CONFIGURED_RESULT_REQUIRED"
