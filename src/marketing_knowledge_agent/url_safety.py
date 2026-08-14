"""Offline raw-URL security validation followed by safe canonicalization."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import SplitResult, unquote, urlsplit

from .cell_normalization import SourceFieldLineage, SourceLineage
from .link_resolution import AssetSourceSlot, LinkCandidate, LinkSource


_POLICY_VERSION = "wp7-v1-2026-08-09"
_MAX_RAW_URL_LENGTH = 4096
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "apikey",
        "auth",
        "signature",
        "session",
        "credential",
        "accesstoken",
        "oauthtoken",
        "refreshtoken",
        "idtoken",
        "authtoken",
        "bearertoken",
        "sig",
        "sessionid",
        "authorization",
        "credentialid",
    }
)
_TRACKING_QUERY_KEYS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
)
_SHORTENER_HOSTS = frozenset({"bit.ly", "goo.gl", "reurl.cc", "tinyurl.com"})
_REDIRECT_HOST_PATHS = frozenset(
    {
        ("google.com", "/search"),
        ("www.google.com", "/search"),
        ("google.com", "/url"),
        ("www.google.com", "/url"),
        ("l.facebook.com", "/l.php"),
    }
)
_INTERNAL_LABELS = frozenset({"internal", "admin"})
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_ASCII_WHITESPACE = frozenset(" \t\n\r\f\v")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_HOST_COMPONENT = re.compile(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)\Z")
_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class URLValidationError(ValueError):
    """Stable contract error that never reflects caller-controlled payloads."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class URLRejectionCode(str, Enum):
    UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"
    MISSING_HOST = "MISSING_HOST"
    USERINFO_NOT_ALLOWED = "USERINFO_NOT_ALLOWED"
    LOCAL_HOST_NOT_ALLOWED = "LOCAL_HOST_NOT_ALLOWED"
    NON_PUBLIC_IP_NOT_ALLOWED = "NON_PUBLIC_IP_NOT_ALLOWED"
    SENSITIVE_QUERY = "SENSITIVE_QUERY"
    CONTROL_CHARACTER = "CONTROL_CHARACTER"
    AMBIGUOUS_URL = "AMBIGUOUS_URL"
    INVALID_PORT = "INVALID_PORT"
    UNSAFE_INTERNAL_TARGET = "UNSAFE_INTERNAL_TARGET"
    REDIRECTOR_NOT_ALLOWED = "REDIRECTOR_NOT_ALLOWED"
    OVERLONG_URL = "OVERLONG_URL"
    IDNA_ERROR = "IDNA_ERROR"


@dataclass(frozen=True)
class URLPolicy:
    """The fixed, user-approved WP7 v1 local policy."""

    version: str = _POLICY_VERSION
    max_url_length: int = _MAX_RAW_URL_LENGTH

    def __post_init__(self) -> None:
        if (
            type(self.version) is not str
            or self.version != _POLICY_VERSION
            or type(self.max_url_length) is not int
            or self.max_url_length != _MAX_RAW_URL_LENGTH
        ):
            raise URLValidationError("URL_POLICY_UNSUPPORTED")


DEFAULT_URL_POLICY = URLPolicy()


@dataclass(frozen=True, init=False)
class CanonicalURL:
    """Accepted offline ref; not safe-to-fetch, SSRF safety, or network authority."""

    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, repr=False)
class URLValidationResult:
    """Accepted canonical URL or redacted rejection with safe WP6 provenance."""

    canonical_url: Optional[CanonicalURL]
    rejection_code: Optional[URLRejectionCode]
    source: LinkSource
    asset_source_slot: AssetSourceSlot
    lineage: SourceLineage
    field_lineage: SourceFieldLineage
    run_start_index: Optional[int]
    run_ordinal: Optional[int]

    def __post_init__(self) -> None:
        if (self.canonical_url is None) == (self.rejection_code is None):
            raise URLValidationError("URL_VALIDATION_RESULT_STATE_INVALID")

    @property
    def is_accepted(self) -> bool:
        return self.canonical_url is not None

    @property
    def is_rejected(self) -> bool:
        return self.rejection_code is not None

    def __repr__(self) -> str:
        return (
            "URLValidationResult("
            f"accepted={self.is_accepted!r}, "
            f"canonical_url={self.canonical_url!r}, "
            f"rejection_code={self.rejection_code!r}, "
            f"source={self.source.value!r}, "
            f"asset_source_slot={self.asset_source_slot.value!r}, "
            f"sheet_id={self.lineage.sheet_id!r}, "
            f"source_coordinate={self.lineage.source_coordinate!r}, "
            f"run_start_index={self.run_start_index!r}, "
            f"run_ordinal={self.run_ordinal!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.is_accepted,
            "canonical_url": (
                self.canonical_url.value if self.canonical_url is not None else None
            ),
            "rejection_code": (
                self.rejection_code.value if self.rejection_code is not None else None
            ),
            "source": self.source.value,
            "asset_source_slot": self.asset_source_slot.value,
            "sheet_id": self.lineage.sheet_id,
            "source_coordinate": list(self.lineage.source_coordinate),
            "field_name": self.field_lineage.field_name,
            "target_coordinate": list(self.field_lineage.target_coordinate),
            "run_start_index": self.run_start_index,
            "run_ordinal": self.run_ordinal,
        }


def _validate_and_canonicalize_raw_url(
    raw_url: str,
    policy: URLPolicy,
) -> Union[str, URLRejectionCode]:
    """Return a canonical value or typed rejection without retaining raw input."""

    # User-approved WP7 Decision 1: measure the untouched WP6 raw URL first.
    if len(raw_url) > policy.max_url_length:
        return URLRejectionCode.OVERLONG_URL
    if _contains_ascii_control(raw_url):
        return URLRejectionCode.CONTROL_CHARACTER
    if _has_edge_whitespace(raw_url) or "\\" in raw_url:
        return URLRejectionCode.AMBIGUOUS_URL
    if not _has_valid_percent_escapes(raw_url):
        return URLRejectionCode.AMBIGUOUS_URL

    try:
        parsed = urlsplit(raw_url)
    except (UnicodeError, ValueError):
        return URLRejectionCode.AMBIGUOUS_URL

    scheme = parsed.scheme.casefold()
    if scheme not in _ALLOWED_SCHEMES:
        return URLRejectionCode.UNSUPPORTED_SCHEME
    if not parsed.netloc or parsed.hostname is None or not parsed.hostname:
        return URLRejectionCode.MISSING_HOST
    if parsed.username is not None or parsed.password is not None:
        return URLRejectionCode.USERINFO_NOT_ALLOWED
    if parsed.netloc.endswith(":"):
        return URLRejectionCode.INVALID_PORT

    try:
        port = parsed.port
    except ValueError:
        return URLRejectionCode.INVALID_PORT

    hostname = parsed.hostname
    if hostname.endswith(".") or "%" in hostname:
        return URLRejectionCode.AMBIGUOUS_URL

    host_result = _validate_host(hostname)
    if isinstance(host_result, URLRejectionCode):
        return host_result
    canonical_host, literal_ip = host_result

    if canonical_host == "localhost" or canonical_host.endswith(".local"):
        return URLRejectionCode.LOCAL_HOST_NOT_ALLOWED
    if any(label in _INTERNAL_LABELS for label in canonical_host.split(".")):
        return URLRejectionCode.UNSAFE_INTERNAL_TARGET
    if literal_ip is not None and _is_non_public_ip(literal_ip):
        return URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED
    if _has_internal_path_segment(parsed.path):
        return URLRejectionCode.UNSAFE_INTERNAL_TARGET

    try:
        sensitive_query = _has_sensitive_query_key(parsed.query)
    except (UnicodeError, ValueError):
        return URLRejectionCode.AMBIGUOUS_URL
    if sensitive_query:
        return URLRejectionCode.SENSITIVE_QUERY

    if _contains_unencoded_whitespace(parsed):
        return URLRejectionCode.AMBIGUOUS_URL
    if canonical_host in _SHORTENER_HOSTS or (
        canonical_host,
        _normalize_path_percent_encoding(parsed.path).casefold(),
    ) in _REDIRECT_HOST_PATHS:
        return URLRejectionCode.REDIRECTOR_NOT_ALLOWED

    return _canonicalize_safe_url(
        raw_url=raw_url,
        parsed=parsed,
        scheme=scheme,
        canonical_host=canonical_host,
        literal_ip=literal_ip,
        port=port,
    )


def _build_canonical_url_authority():
    # Guard scope: docs/governance/PYTHON_GUARD_THREAT_MODEL.md (T1/T2, not T3).
    authorization = object()

    def canonical_url_init(self, value, _validation_token=None):
        if _validation_token is not authorization or type(value) is not str:
            raise URLValidationError("CANONICAL_URL_VALIDATION_REQUIRED")
        object.__setattr__(self, "value", value)

    def validate_and_canonicalize_url(
        candidate: LinkCandidate,
        policy: URLPolicy = DEFAULT_URL_POLICY,
    ) -> URLValidationResult:
        """Validate one WP6 candidate's raw URL before canonicalizing safe input."""

        if type(candidate) is not LinkCandidate:
            raise URLValidationError("LINK_CANDIDATE_REQUIRED")
        if type(policy) is not URLPolicy:
            raise URLValidationError("URL_POLICY_REQUIRED")

        result = _validate_and_canonicalize_raw_url(candidate.raw_url, policy)
        if isinstance(result, URLRejectionCode):
            return _rejected(candidate, result)
        return _accepted(
            candidate,
            CanonicalURL(result, _validation_token=authorization),
        )

    def validate_and_canonicalize_evidence_url(
        raw_url: str,
        policy: URLPolicy = DEFAULT_URL_POLICY,
    ) -> CanonicalURL:
        """Validate raw metric evidence and return only a safe canonical URL."""

        if type(raw_url) is not str:
            raise URLValidationError("RAW_URL_REQUIRED")
        if type(policy) is not URLPolicy:
            raise URLValidationError("URL_POLICY_REQUIRED")

        result = _validate_and_canonicalize_raw_url(raw_url, policy)
        if isinstance(result, URLRejectionCode):
            raise URLValidationError(result.value)
        return CanonicalURL(result, _validation_token=authorization)

    canonical_url_init.__name__ = "__init__"
    canonical_url_init.__qualname__ = "CanonicalURL.__init__"
    validate_and_canonicalize_url.__qualname__ = "validate_and_canonicalize_url"
    validate_and_canonicalize_evidence_url.__qualname__ = (
        "validate_and_canonicalize_evidence_url"
    )
    return (
        canonical_url_init,
        validate_and_canonicalize_url,
        validate_and_canonicalize_evidence_url,
    )


(
    _canonical_url_init,
    validate_and_canonicalize_url,
    validate_and_canonicalize_evidence_url,
) = _build_canonical_url_authority()
CanonicalURL.__init__ = _canonical_url_init
del _canonical_url_init
del _build_canonical_url_authority


def _accepted(
    candidate: LinkCandidate,
    canonical_url: CanonicalURL,
) -> URLValidationResult:
    return _result(candidate, canonical_url=canonical_url, rejection_code=None)


def _rejected(
    candidate: LinkCandidate,
    rejection_code: URLRejectionCode,
) -> URLValidationResult:
    return _result(candidate, canonical_url=None, rejection_code=rejection_code)


def _result(
    candidate: LinkCandidate,
    *,
    canonical_url: Optional[CanonicalURL],
    rejection_code: Optional[URLRejectionCode],
) -> URLValidationResult:
    return URLValidationResult(
        canonical_url=canonical_url,
        rejection_code=rejection_code,
        source=candidate.source,
        asset_source_slot=candidate.asset_source_slot,
        lineage=candidate.lineage,
        field_lineage=candidate.field_lineage,
        run_start_index=candidate.run_start_index,
        run_ordinal=candidate.run_ordinal,
    )


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _has_edge_whitespace(value: str) -> bool:
    return bool(value) and (value[0].isspace() or value[-1].isspace())


def _has_valid_percent_escapes(value: str) -> bool:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if index + 2 >= len(value):
            return False
        try:
            int(value[index + 1 : index + 3], 16)
        except ValueError:
            return False
        index += 3
    return True


def _validate_host(
    hostname: str,
) -> Union[Tuple[str, Optional[_IPAddress]], URLRejectionCode]:
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        return str(literal_ip).casefold(), literal_ip
    if _looks_like_nonstandard_ip(hostname):
        return URLRejectionCode.AMBIGUOUS_URL

    try:
        canonical_host = hostname.encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return URLRejectionCode.IDNA_ERROR

    # IDNA can turn Unicode digits into a literal or ambiguous numeric IP form.
    try:
        literal_ip = ipaddress.ip_address(canonical_host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        return str(literal_ip).casefold(), literal_ip
    if _looks_like_nonstandard_ip(canonical_host):
        return URLRejectionCode.AMBIGUOUS_URL

    if len(canonical_host) > 253:
        return URLRejectionCode.AMBIGUOUS_URL
    labels = canonical_host.split(".")
    if any(_HOST_LABEL.fullmatch(label) is None for label in labels):
        return URLRejectionCode.AMBIGUOUS_URL
    return canonical_host, None


def _looks_like_nonstandard_ip(hostname: str) -> bool:
    if _NUMERIC_HOST_COMPONENT.fullmatch(hostname) is not None:
        return True
    components = hostname.split(".")
    return len(components) > 1 and all(
        _NUMERIC_HOST_COMPONENT.fullmatch(component) is not None
        for component in components
    )


def _is_non_public_ip(address: _IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def _has_internal_path_segment(path: str) -> bool:
    return any(
        _normalize_path_percent_encoding(segment).casefold() in _INTERNAL_LABELS
        for segment in path.split("/")
    )


def _has_sensitive_query_key(query: str) -> bool:
    for occurrence in query.split("&"):
        raw_key = occurrence.partition("=")[0]
        decoded_key = unquote(raw_key, encoding="utf-8", errors="strict")
        if "%" in decoded_key:
            raise ValueError("ambiguous query key")
        normalized_key = "".join(
            character
            for character in decoded_key.casefold()
            if character not in _ASCII_WHITESPACE and character not in "_-."
        )
        if normalized_key in _SENSITIVE_QUERY_KEYS:
            return True
    return False


def _contains_unencoded_whitespace(parsed: SplitResult) -> bool:
    return any(
        character.isspace()
        for component in (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
        for character in component
    )


def _canonicalize_safe_url(
    *,
    raw_url: str,
    parsed: SplitResult,
    scheme: str,
    canonical_host: str,
    literal_ip: Optional[_IPAddress],
    port: Optional[int],
) -> str:
    if isinstance(literal_ip, ipaddress.IPv6Address):
        netloc = f"[{canonical_host}]"
    else:
        netloc = canonical_host
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"

    path = _normalize_path_percent_encoding(parsed.path)
    query, tracking_removed = _remove_tracking_query_occurrences(parsed.query)
    has_explicit_query = "?" in raw_url.partition("#")[0]

    canonical = f"{scheme}://{netloc}{path}"
    if query or (has_explicit_query and not tracking_removed):
        canonical = f"{canonical}?{query}"
    return canonical


def _normalize_path_percent_encoding(path: str) -> str:
    output = []
    index = 0
    while index < len(path):
        character = path[index]
        if character != "%":
            output.append(character)
            index += 1
            continue
        value = int(path[index + 1 : index + 3], 16)
        decoded = chr(value)
        if decoded in _UNRESERVED:
            output.append(decoded)
        else:
            output.append(f"%{value:02X}")
        index += 3
    return "".join(output)


def _remove_tracking_query_occurrences(query: str) -> Tuple[str, bool]:
    kept = []
    removed = False
    for occurrence in query.split("&"):
        raw_key = occurrence.partition("=")[0]
        decoded_key = unquote(raw_key, encoding="utf-8", errors="strict").casefold()
        if decoded_key.startswith("utm_") or decoded_key in _TRACKING_QUERY_KEYS:
            removed = True
            continue
        kept.append(occurrence)
    return "&".join(kept), removed


__all__ = [
    "CanonicalURL",
    "DEFAULT_URL_POLICY",
    "URLPolicy",
    "URLRejectionCode",
    "URLValidationError",
    "URLValidationResult",
    "validate_and_canonicalize_evidence_url",
    "validate_and_canonicalize_url",
]
