"""Narrow read-only Google Sheets transport for the frozen S1-WP1 request."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time
from typing import Any, Callable, Mapping, Protocol, Tuple

from google.auth.transport.requests import AuthorizedSession
from requests import Response
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectTimeout, ReadTimeout, RequestException, Timeout

from .google_sheets_read_contracts import ConfiguredReadResult
from .google_sheets_response_mapper import (
    GoogleSheetsResponseError,
    map_google_sheets_response,
)
from .google_sheets_runtime_config import (
    GoogleSheetsRuntimeConfig,
    production_google_sheets_runtime_config,
)


class GoogleSheetsTransportError(RuntimeError):
    """Stable, payload-safe transport or credential-boundary failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class CredentialProvider(Protocol):
    """Trusted construction boundary for already-configured credentials."""

    def get_credentials(self, *, scopes: Tuple[str, ...]) -> Any:
        ...


class _Clock(Protocol):
    def monotonic(self) -> float:
        ...

    def utcnow(self) -> datetime:
        ...


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)


class _BlockedAuthRequest:
    """Prevent WP1 from refreshing credentials or making an auth HTTP request."""

    def __call__(self, *args: object, **kwargs: object) -> None:
        _fail("GOOGLE_SHEETS_CREDENTIAL_REFRESH_FORBIDDEN")


class GoogleSheetsTransport:
    """One fixed read operation with no caller-controlled request authority."""

    __slots__ = ("_clock", "_config", "_session", "_sleeper")

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("GOOGLE_SHEETS_TRANSPORT_CONSTRUCTION_FORBIDDEN")

    @classmethod
    def _create(
        cls,
        config: GoogleSheetsRuntimeConfig,
        session: AuthorizedSession,
        clock: _Clock,
        sleeper: Callable[[float], None],
    ) -> "GoogleSheetsTransport":
        transport = object.__new__(cls)
        transport._config = config
        transport._session = session
        transport._clock = clock
        transport._sleeper = sleeper
        return transport

    def read(self) -> ConfiguredReadResult:
        """Execute exactly the frozen GET and return only a coverage-bound result."""

        policy = self._config.transport_policy
        deadline = self._clock.monotonic() + policy.timeout.overall_deadline_seconds
        for attempt in range(1, policy.retry.maximum_attempts + 1):
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                _fail("GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED")
            caught_transport_error = None
            timeout_kind = None
            request_error_code = None
            try:
                response = self._session.request(
                    policy.http_method,
                    policy.endpoint,
                    params=_request_query(policy),
                    data=None,
                    allow_redirects=False,
                    timeout=(
                        policy.timeout.connect_seconds,
                        policy.timeout.read_seconds,
                    ),
                    max_allowed_time=remaining,
                    verify=True,
                )
            except GoogleSheetsTransportError as error:
                caught_transport_error = error
            except ConnectTimeout:
                timeout_kind = "CONNECT_TIMEOUT"
            except ReadTimeout:
                timeout_kind = "READ_TIMEOUT"
            except Timeout:
                request_error_code = "GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED"
            except RequestException:
                request_error_code = "GOOGLE_SHEETS_REQUEST_FAILED"
            except Exception:
                request_error_code = "GOOGLE_SHEETS_AUTH_OR_SESSION_FAILED"

            if caught_transport_error is not None:
                caught_transport_error.__cause__ = None
                caught_transport_error.__context__ = None
                raise caught_transport_error from None
            if timeout_kind is not None:
                if self._retry_timeout(attempt, deadline, timeout_kind):
                    continue
                raise AssertionError("unreachable")
            if request_error_code is not None:
                _fail(request_error_code)

            if self._clock.monotonic() >= deadline:
                _close_response(response)
                _fail("GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED")
            status = response.status_code
            if type(status) is not int:
                _close_response(response)
                _fail("GOOGLE_SHEETS_HTTP_STATUS_INVALID")
            if status == 200:
                payload = _response_json(response)
                self._require_before_deadline(deadline)
                result = _map_result(payload, self._config)
                self._require_before_deadline(deadline)
                return result
            if status in policy.retry.retry_statuses:
                retry_after = response.headers.get("Retry-After")
                _close_response(response)
                if attempt >= policy.retry.maximum_attempts:
                    _fail("GOOGLE_SHEETS_HTTP_RETRY_EXHAUSTED")
                delay = _retry_delay(
                    retry_after,
                    now=self._clock.utcnow(),
                    fallback=policy.retry.fallback_delay_seconds,
                    maximum_delay=max(0.0, deadline - self._clock.monotonic()),
                )
                self._sleep_before_retry(delay, deadline)
                continue

            _close_response(response)
            if 300 <= status <= 399:
                _fail("GOOGLE_SHEETS_REDIRECT_REJECTED")
            if status == 400:
                _fail("GOOGLE_SHEETS_BAD_REQUEST")
            if status == 401:
                _fail("GOOGLE_SHEETS_UNAUTHORIZED")
            if status == 403:
                _fail("GOOGLE_SHEETS_FORBIDDEN")
            if status == 404:
                _fail("GOOGLE_SHEETS_TARGET_NOT_FOUND")
            if 200 <= status <= 299:
                _fail("GOOGLE_SHEETS_NON_200_SUCCESS_REJECTED")
            _fail("GOOGLE_SHEETS_HTTP_STATUS_UNAPPROVED")

        raise AssertionError("unreachable")

    def _retry_timeout(self, attempt: int, deadline: float, kind: str) -> bool:
        policy = self._config.transport_policy
        if attempt >= policy.retry.maximum_attempts:
            _fail(f"GOOGLE_SHEETS_{kind}_RETRY_EXHAUSTED")
        self._sleep_before_retry(policy.retry.fallback_delay_seconds, deadline)
        return True

    def _sleep_before_retry(self, delay: float, deadline: float) -> None:
        remaining = deadline - self._clock.monotonic()
        if delay >= remaining:
            _fail("GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED")
        sleep_failed = False
        try:
            self._sleeper(delay)
        except Exception:
            sleep_failed = True
        if sleep_failed:
            _fail("GOOGLE_SHEETS_RETRY_SLEEP_FAILED")
        if self._clock.monotonic() >= deadline:
            _fail("GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED")

    def _require_before_deadline(self, deadline: float) -> None:
        if self._clock.monotonic() >= deadline:
            _fail("GOOGLE_SHEETS_OVERALL_DEADLINE_EXCEEDED")

    def __repr__(self) -> str:
        return (
            "GoogleSheetsTransport("
            f"transport_policy_version={self._config.transport_policy_version!r}, "
            f"transport_policy_identity={self._config.transport_policy_identity!r})"
        )


def create_google_sheets_transport(
    credential_provider: CredentialProvider,
) -> GoogleSheetsTransport:
    """Construct the fixed transport without discovering or creating credentials."""

    return _create_transport(
        credential_provider,
        adapter=HTTPAdapter(max_retries=0),
        clock=_SystemClock(),
        sleeper=time.sleep,
    )


def _create_google_sheets_transport_for_test(
    credential_provider: CredentialProvider,
    *,
    adapter: HTTPAdapter,
    clock: _Clock,
    sleeper: Callable[[float], None],
) -> GoogleSheetsTransport:
    """Trusted offline seam; request selection remains the production freeze."""

    return _create_transport(
        credential_provider,
        adapter=adapter,
        clock=clock,
        sleeper=sleeper,
    )


def _create_transport(
    credential_provider: CredentialProvider,
    *,
    adapter: HTTPAdapter,
    clock: _Clock,
    sleeper: Callable[[float], None],
) -> GoogleSheetsTransport:
    config = production_google_sheets_runtime_config()
    if not _adapter_retries_are_zero(adapter):
        _fail("GOOGLE_SHEETS_ADAPTER_RETRIES_NOT_ZERO")
    provider_failed = False
    try:
        credentials = credential_provider.get_credentials(
            scopes=config.transport_policy.oauth_scopes
        )
    except Exception:
        provider_failed = True
        credentials = None
    if provider_failed:
        _fail("GOOGLE_SHEETS_CREDENTIAL_PROVIDER_FAILED")
    if credentials is None:
        _fail("GOOGLE_SHEETS_CREDENTIALS_MISSING")
    session_failed = False
    try:
        session = AuthorizedSession(
            credentials,
            refresh_status_codes=(),
            max_refresh_attempts=0,
            auth_request=_BlockedAuthRequest(),
        )
        session.trust_env = config.transport_policy.trust_env
        if session.trust_env is not False:
            _fail("GOOGLE_SHEETS_ENVIRONMENT_AUTHORITY_ENABLED")
        session.mount("https://", adapter)
    except Exception:
        session_failed = True
        session = None
    if session_failed or session is None:
        _fail("GOOGLE_SHEETS_SESSION_CONSTRUCTION_FAILED")
    if session._refresh_status_codes != () or session._max_refresh_attempts != 0:
        _fail("GOOGLE_SHEETS_HIDDEN_REFRESH_RETRY_ENABLED")
    if not _adapter_retries_are_zero(session.get_adapter(config.transport_policy.endpoint)):
        _fail("GOOGLE_SHEETS_ADAPTER_RETRIES_NOT_ZERO")
    return GoogleSheetsTransport._create(config, session, clock, sleeper)


def _adapter_retries_are_zero(adapter: HTTPAdapter) -> bool:
    retries = getattr(adapter, "max_retries", None)
    return getattr(retries, "total", None) == 0


def _request_query(policy: Any) -> Tuple[Tuple[str, str], ...]:
    return (
        tuple(("ranges", value) for value in policy.request_ranges)
        + (("fields", policy.fields_selector),)
        + (("includeGridData", "true"),)
    )


def _response_json(response: Response) -> Mapping[str, object]:
    json_failed = False
    try:
        payload = response.json()
    except Exception:
        json_failed = True
        payload = None
    if json_failed:
        _close_response(response)
        _fail("GOOGLE_SHEETS_RESPONSE_JSON_INVALID")
    _close_response(response)
    if not isinstance(payload, Mapping):
        _fail("GOOGLE_SHEETS_RESPONSE_SHAPE_INVALID")
    return payload


def _close_response(response: Response) -> None:
    close_failed = False
    try:
        response.close()
    except Exception:
        close_failed = True
    if close_failed:
        _fail("GOOGLE_SHEETS_RESPONSE_CLOSE_FAILED")


def _map_result(
    payload: Mapping[str, object], config: GoogleSheetsRuntimeConfig
) -> ConfiguredReadResult:
    mapper_error = None
    mapper_failed = False
    try:
        result = map_google_sheets_response(payload, config.read_plan)
    except GoogleSheetsResponseError as error:
        mapper_error = error
        result = None
    except Exception:
        mapper_failed = True
        result = None
    if mapper_error is not None:
        mapper_error.__cause__ = None
        mapper_error.__context__ = None
        raise mapper_error from None
    if mapper_failed:
        _fail("GOOGLE_SHEETS_MAPPER_FAILED")
    if not isinstance(result, ConfiguredReadResult):
        _fail("GOOGLE_SHEETS_CONFIGURED_RESULT_REQUIRED")
    return result


def _retry_delay(
    value: object,
    *,
    now: datetime,
    fallback: float,
    maximum_delay: float,
) -> float:
    if not isinstance(value, str):
        return fallback
    stripped = value.strip()
    if stripped and all("0" <= character <= "9" for character in stripped):
        significant = stripped.lstrip("0")
        if not significant:
            return 0.0
        maximum_digits = len(str(max(1, math.ceil(maximum_delay))))
        if len(significant) > maximum_digits:
            return maximum_delay
        seconds = float(significant)
        return min(seconds, maximum_delay)
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - current).total_seconds())


def _fail(code: str) -> None:
    raise GoogleSheetsTransportError(code) from None


__all__ = [
    "CredentialProvider",
    "GoogleSheetsTransport",
    "GoogleSheetsTransportError",
    "create_google_sheets_transport",
]
