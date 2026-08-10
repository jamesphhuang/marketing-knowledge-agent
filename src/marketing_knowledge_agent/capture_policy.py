"""Offline WP10 capture-policy and fetch-outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple


class CapturePolicyError(ValueError):
    """Stable contract error that never reflects caller-controlled payloads."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CaptureMode(str, Enum):
    FULL_TEXT = "full_text"
    METADATA_ONLY = "metadata_only"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


class DomainClass(str, Enum):
    SHOPLINE_OWNED = "shopline_owned"
    APPROVED_THIRD_PARTY = "approved_third_party"
    UNKNOWN_THIRD_PARTY = "unknown_third_party"
    AUTHENTICATED_OR_PAYWALLED = "authenticated_or_paywalled"
    UNSAFE_PRIVATE_OR_INTERNAL = "unsafe_private_or_internal"


class PolicyDecisionReason(str, Enum):
    SHOPLINE_OWNED = "shopline_owned"
    APPROVED_THIRD_PARTY_RULE = "approved_third_party_rule"
    NEEDS_POLICY = "needs_policy"
    AUTHENTICATED_OR_PAYWALLED = "authenticated_or_paywalled"
    UNSAFE_PRIVATE_OR_INTERNAL = "unsafe_private_or_internal"
    POLICY_MISSING = "policy_missing"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, repr=False)
class ApprovedDomainRule:
    """One exact, already-normalized approved third-party domain rule."""

    domain_key: str
    mode: CaptureMode

    def __post_init__(self) -> None:
        _validate_strict_text(self.domain_key, "CAPTURE_DOMAIN_KEY_INVALID")
        if type(self.mode) is not CaptureMode or self.mode not in (
            CaptureMode.FULL_TEXT,
            CaptureMode.METADATA_ONLY,
        ):
            raise CapturePolicyError("APPROVED_DOMAIN_RULE_MODE_INVALID")

    def __repr__(self) -> str:
        return f"ApprovedDomainRule(domain_key=<redacted>, mode={self.mode.value!r})"


@dataclass(frozen=True, repr=False)
class CapturePolicy:
    """Caller-supplied immutable v1 policy with exact domain matching."""

    policy_version: str
    approved_domain_rules: Tuple[ApprovedDomainRule, ...] = ()

    def __post_init__(self) -> None:
        _validate_strict_text(
            self.policy_version,
            "CAPTURE_POLICY_VERSION_INVALID",
        )
        if type(self.approved_domain_rules) is not tuple:
            raise CapturePolicyError("CAPTURE_POLICY_RULES_TUPLE_REQUIRED")

        seen_domain_keys = set()
        for rule in self.approved_domain_rules:
            if type(rule) is not ApprovedDomainRule:
                raise CapturePolicyError("CAPTURE_POLICY_RULE_REQUIRED")
            if rule.domain_key in seen_domain_keys:
                raise CapturePolicyError("CAPTURE_POLICY_DUPLICATE_DOMAIN_RULE")
            seen_domain_keys.add(rule.domain_key)

    def __repr__(self) -> str:
        return (
            "CapturePolicy("
            "policy_version=<redacted>, "
            f"approved_domain_rule_count={len(self.approved_domain_rules)})"
        )


_VALID_POLICY_DECISIONS = {
    PolicyDecisionReason.SHOPLINE_OWNED: (CaptureMode.FULL_TEXT,),
    PolicyDecisionReason.APPROVED_THIRD_PARTY_RULE: (
        CaptureMode.FULL_TEXT,
        CaptureMode.METADATA_ONLY,
    ),
    PolicyDecisionReason.NEEDS_POLICY: (CaptureMode.METADATA_ONLY,),
    PolicyDecisionReason.AUTHENTICATED_OR_PAYWALLED: (CaptureMode.BLOCKED,),
    PolicyDecisionReason.UNSAFE_PRIVATE_OR_INTERNAL: (CaptureMode.BLOCKED,),
    PolicyDecisionReason.POLICY_MISSING: (CaptureMode.BLOCKED,),
    PolicyDecisionReason.UNSUPPORTED: (CaptureMode.UNSUPPORTED,),
}


@dataclass(frozen=True, repr=False)
class CapturePolicyDecision:
    """Auditable policy decision without lifecycle or fetch-success state."""

    mode: CaptureMode
    reason: PolicyDecisionReason
    policy_version: str

    def __post_init__(self) -> None:
        _validate_strict_text(
            self.policy_version,
            "CAPTURE_POLICY_VERSION_INVALID",
        )
        if type(self.mode) is not CaptureMode:
            raise CapturePolicyError("CAPTURE_POLICY_DECISION_INVALID")
        if type(self.reason) is not PolicyDecisionReason:
            raise CapturePolicyError("CAPTURE_POLICY_DECISION_INVALID")
        if self.mode not in _VALID_POLICY_DECISIONS[self.reason]:
            raise CapturePolicyError("CAPTURE_POLICY_DECISION_INVALID")

    def __repr__(self) -> str:
        return (
            "CapturePolicyDecision("
            f"mode={self.mode.value!r}, "
            f"reason={self.reason.value!r}, "
            "policy_version=<redacted>)"
        )


def evaluate_capture_policy(
    policy: CapturePolicy,
    domain_class: DomainClass,
    *,
    domain_key: Optional[str] = None,
) -> CapturePolicyDecision:
    """Evaluate one upstream-classified target without parsing a URL."""

    if type(policy) is not CapturePolicy:
        raise CapturePolicyError("CAPTURE_POLICY_REQUIRED")
    if type(domain_class) is not DomainClass:
        raise CapturePolicyError("DOMAIN_CLASS_REQUIRED")

    if domain_class is DomainClass.SHOPLINE_OWNED:
        return _decision(
            policy,
            CaptureMode.FULL_TEXT,
            PolicyDecisionReason.SHOPLINE_OWNED,
        )
    if domain_class is DomainClass.UNKNOWN_THIRD_PARTY:
        return _decision(
            policy,
            CaptureMode.METADATA_ONLY,
            PolicyDecisionReason.NEEDS_POLICY,
        )
    if domain_class is DomainClass.AUTHENTICATED_OR_PAYWALLED:
        return _decision(
            policy,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.AUTHENTICATED_OR_PAYWALLED,
        )
    if domain_class is DomainClass.UNSAFE_PRIVATE_OR_INTERNAL:
        return _decision(
            policy,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.UNSAFE_PRIVATE_OR_INTERNAL,
        )

    if domain_key is None:
        raise CapturePolicyError("CAPTURE_DOMAIN_KEY_REQUIRED")
    _validate_strict_text(domain_key, "CAPTURE_DOMAIN_KEY_INVALID")
    for rule in policy.approved_domain_rules:
        if rule.domain_key == domain_key:
            return _decision(
                policy,
                rule.mode,
                PolicyDecisionReason.APPROVED_THIRD_PARTY_RULE,
            )
    return _decision(
        policy,
        CaptureMode.BLOCKED,
        PolicyDecisionReason.POLICY_MISSING,
    )


def _decision(
    policy: CapturePolicy,
    mode: CaptureMode,
    reason: PolicyDecisionReason,
) -> CapturePolicyDecision:
    return CapturePolicyDecision(
        mode=mode,
        reason=reason,
        policy_version=policy.policy_version,
    )


@dataclass(frozen=True, repr=False)
class ValidatedCaptureTargetRef:
    """Opaque internal reference created only after upstream validation."""

    value: str

    def __post_init__(self) -> None:
        _validate_strict_text(self.value, "CAPTURE_TARGET_REF_INVALID")

    def to_serializable_value(self) -> str:
        """Return the opaque value only for intentional canonical serialization."""

        return self.value

    def __repr__(self) -> str:
        return "ValidatedCaptureTargetRef(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-capture-target>"


_EXECUTABLE_CAPTURE_MODES = (
    CaptureMode.FULL_TEXT,
    CaptureMode.METADATA_ONLY,
)


@dataclass(frozen=True, repr=False)
class CaptureRequest:
    """Executable request contract produced only after a permitting decision."""

    target_ref: ValidatedCaptureTargetRef
    decision: CapturePolicyDecision

    def __post_init__(self) -> None:
        if type(self.target_ref) is not ValidatedCaptureTargetRef:
            raise CapturePolicyError("VALIDATED_CAPTURE_TARGET_REF_REQUIRED")
        if type(self.decision) is not CapturePolicyDecision:
            raise CapturePolicyError("CAPTURE_POLICY_DECISION_REQUIRED")
        if self.decision.mode not in _EXECUTABLE_CAPTURE_MODES:
            raise CapturePolicyError("CAPTURE_REQUEST_MODE_NOT_EXECUTABLE")

    @property
    def policy_version(self) -> str:
        return self.decision.policy_version

    def __repr__(self) -> str:
        return (
            "CaptureRequest("
            "target_ref=<redacted>, "
            f"mode={self.decision.mode.value!r}, "
            "policy_version=<redacted>)"
        )


class FetchFailureCategory(str, Enum):
    TEMPORARY = "temporary"
    NON_TEMPORARY = "non_temporary"


class FetchFailureReason(str, Enum):
    TIMEOUT = "timeout"
    TEMPORARY_DNS = "temporary_dns"
    TEMPORARY_NETWORK = "temporary_network"
    HTTP_STATUS = "http_status"
    POLICY_BLOCKED = "policy_blocked"
    AUTHENTICATED_OR_PAYWALLED = "authenticated_or_paywalled"
    GOVERNANCE_REJECTED = "governance_rejected"
    IDENTITY_RECONCILIATION_FAILED = "identity_reconciliation_failed"
    UNSAFE_TARGET = "unsafe_target"


_TEMPORARY_FAILURE_REASONS = (
    FetchFailureReason.TIMEOUT,
    FetchFailureReason.TEMPORARY_DNS,
    FetchFailureReason.TEMPORARY_NETWORK,
)
_FETCH_OUTCOME_FAILURE_REASONS = _TEMPORARY_FAILURE_REASONS + (
    FetchFailureReason.HTTP_STATUS,
)


def classify_fetch_failure(
    reason: FetchFailureReason,
    *,
    status_code: Optional[int] = None,
) -> FetchFailureCategory:
    """Classify one synthetic failure without retry or lifecycle decisions."""

    if type(reason) is not FetchFailureReason:
        raise CapturePolicyError("FETCH_FAILURE_REASON_REQUIRED")
    if reason is FetchFailureReason.HTTP_STATUS:
        if type(status_code) is not int or not 400 <= status_code <= 599:
            raise CapturePolicyError("FETCH_FAILURE_HTTP_STATUS_INVALID")
        if status_code == 429 or status_code >= 500:
            return FetchFailureCategory.TEMPORARY
        return FetchFailureCategory.NON_TEMPORARY
    if status_code is not None:
        raise CapturePolicyError("FETCH_FAILURE_HTTP_STATUS_UNEXPECTED")
    if reason in _TEMPORARY_FAILURE_REASONS:
        return FetchFailureCategory.TEMPORARY
    return FetchFailureCategory.NON_TEMPORARY


@dataclass(frozen=True)
class FetchResult:
    """Minimal typed outcome for a single future fetch attempt."""

    succeeded: bool
    failure_reason: Optional[FetchFailureReason] = None
    http_status_code: Optional[int] = None

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise CapturePolicyError("FETCH_RESULT_SUCCEEDED_BOOL_REQUIRED")
        if self.succeeded:
            if self.failure_reason is not None or self.http_status_code is not None:
                raise CapturePolicyError("FETCH_RESULT_SUCCESS_STATE_INVALID")
            return
        if type(self.failure_reason) is not FetchFailureReason:
            raise CapturePolicyError("FETCH_RESULT_FAILURE_REASON_REQUIRED")
        if self.failure_reason not in _FETCH_OUTCOME_FAILURE_REASONS:
            raise CapturePolicyError("FETCH_RESULT_REASON_NOT_FETCH_OUTCOME")
        classify_fetch_failure(
            self.failure_reason,
            status_code=self.http_status_code,
        )

    @property
    def failure_category(self) -> Optional[FetchFailureCategory]:
        if self.succeeded:
            return None
        return classify_fetch_failure(
            self.failure_reason,
            status_code=self.http_status_code,
        )


class FetchClient(Protocol):
    """Interface only; WP10 intentionally provides no implementation."""

    def fetch(self, request: CaptureRequest) -> FetchResult:
        ...


def _validate_strict_text(value: object, error_code: str) -> None:
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or _contains_ascii_control(value)
    ):
        raise CapturePolicyError(error_code)


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
