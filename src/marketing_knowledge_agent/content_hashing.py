"""Pure capture hashing, revision, and Last Known Good contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .capture_policy import (
    CaptureMode,
    CapturePolicyDecision,
    FetchFailureCategory,
)
from .captured_content import (
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
)
from .html_normalization import HtmlNormalizationResult, NormalizationStatus
from .url_safety import CanonicalURL


_CAPTURE_HASH_DOMAIN = b"MKA_CAPTURE_CONTENT_HASH_V1\x00"
_SHA256_PREFIX = "sha256:"
_SHA256_HEX_LENGTH = 64


class ContentHashingError(ValueError):
    """Stable WP12 failure containing no captured body or URL payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CaptureContentHash(str):
    """Typed SHA-256 serialization for normalized captured content."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if type(value) is not str or not _is_capture_hash(value):
            raise ContentHashingError("CAPTURE_CONTENT_HASH_INVALID")
        return str.__new__(cls, value)


def compute_capture_content_hash(
    result: HtmlNormalizationResult,
) -> CaptureContentHash:
    """Hash one exact successful WP11 normalized result."""

    if type(result) is not HtmlNormalizationResult:
        raise ContentHashingError("HTML_NORMALIZATION_RESULT_REQUIRED")
    if result.status is not NormalizationStatus.SUCCESS or result.clean_body is None:
        raise ContentHashingError("HTML_NORMALIZATION_SUCCESS_REQUIRED")

    try:
        parser_version = result.parser_version.encode("utf-8", errors="strict")
        clean_body = result.clean_body.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ContentHashingError("CAPTURE_HASH_INPUT_UTF8_INVALID") from None

    payload = b"".join(
        (
            _CAPTURE_HASH_DOMAIN,
            len(parser_version).to_bytes(8, "big"),
            parser_version,
            len(clean_body).to_bytes(8, "big"),
            clean_body,
        )
    )
    digest = hashlib.sha256(payload).hexdigest()
    return CaptureContentHash(f"{_SHA256_PREFIX}{digest}")


@dataclass(frozen=True)
class CaptureRevisionRef:
    captured_content_id: CapturedContentId
    content_hash: CaptureContentHash
    parser_version: str

    def __post_init__(self) -> None:
        if type(self.captured_content_id) is not CapturedContentId:
            raise ContentHashingError("CAPTURE_REVISION_CONTENT_ID_INVALID")
        if type(self.content_hash) is not CaptureContentHash:
            raise ContentHashingError("CAPTURE_REVISION_HASH_INVALID")
        _validate_text(
            self.parser_version,
            "CAPTURE_REVISION_PARSER_VERSION_INVALID",
        )


class RevisionDisposition(str, Enum):
    SAME_CONTENT = "same_content"
    NEW_REVISION = "new_revision"


class RevisionReason(str, Enum):
    SAME_CONTENT = "same_content"
    FIRST_SUCCESS = "first_success"
    BODY_CHANGED = "body_changed"
    PARSER_VERSION_CHANGED = "parser_version_changed"


_REVISION_DECISION_PAIRS = {
    (RevisionDisposition.SAME_CONTENT, RevisionReason.SAME_CONTENT),
    (RevisionDisposition.NEW_REVISION, RevisionReason.FIRST_SUCCESS),
    (RevisionDisposition.NEW_REVISION, RevisionReason.BODY_CHANGED),
    (RevisionDisposition.NEW_REVISION, RevisionReason.PARSER_VERSION_CHANGED),
}


@dataclass(frozen=True)
class RevisionDecision:
    disposition: RevisionDisposition
    reason: RevisionReason

    def __post_init__(self) -> None:
        if (
            type(self.disposition) is not RevisionDisposition
            or type(self.reason) is not RevisionReason
            or (self.disposition, self.reason) not in _REVISION_DECISION_PAIRS
        ):
            raise ContentHashingError("CAPTURE_REVISION_DECISION_INVALID")


def decide_capture_revision(
    previous_revision: Optional[CaptureRevisionRef],
    current_revision: CaptureRevisionRef,
) -> RevisionDecision:
    """Compare two refs within one logical capture lineage."""

    if previous_revision is not None and type(previous_revision) is not CaptureRevisionRef:
        raise ContentHashingError("PREVIOUS_CAPTURE_REVISION_INVALID")
    if type(current_revision) is not CaptureRevisionRef:
        raise ContentHashingError("CURRENT_CAPTURE_REVISION_INVALID")
    if previous_revision is None:
        return RevisionDecision(
            RevisionDisposition.NEW_REVISION,
            RevisionReason.FIRST_SUCCESS,
        )
    if previous_revision.captured_content_id != current_revision.captured_content_id:
        raise ContentHashingError("CAPTURE_REVISION_LINEAGE_MISMATCH")
    if previous_revision.parser_version != current_revision.parser_version:
        return RevisionDecision(
            RevisionDisposition.NEW_REVISION,
            RevisionReason.PARSER_VERSION_CHANGED,
        )
    if previous_revision.content_hash == current_revision.content_hash:
        return RevisionDecision(
            RevisionDisposition.SAME_CONTENT,
            RevisionReason.SAME_CONTENT,
        )
    return RevisionDecision(
        RevisionDisposition.NEW_REVISION,
        RevisionReason.BODY_CHANGED,
    )


@dataclass(frozen=True, repr=False)
class ApprovedLkgFreshnessPolicy:
    policy_version: str
    max_age: timedelta

    def __post_init__(self) -> None:
        _validate_text(
            self.policy_version,
            "LKG_FRESHNESS_POLICY_VERSION_INVALID",
        )
        if type(self.max_age) is not timedelta or self.max_age <= timedelta(0):
            raise ContentHashingError("LKG_FRESHNESS_MAX_AGE_INVALID")

    def __repr__(self) -> str:
        return (
            "ApprovedLkgFreshnessPolicy("
            "policy_version=<redacted>, "
            f"max_age={self.max_age!r})"
        )


@dataclass(frozen=True, repr=False)
class LkgEligibilityInput:
    current_canonical_url: CanonicalURL
    previous_success: Optional[CapturedContent]
    current_capture_policy: CapturePolicyDecision
    current_failure_category: Optional[FetchFailureCategory]
    governance_allowed: bool
    identity_reconciled: bool
    freshness_policy: Optional[ApprovedLkgFreshnessPolicy]
    current_attempt_at: Optional[datetime]

    def __post_init__(self) -> None:
        if type(self.current_canonical_url) is not CanonicalURL:
            raise ContentHashingError("LKG_CANONICAL_URL_INVALID")
        if self.previous_success is not None and type(self.previous_success) is not CapturedContent:
            raise ContentHashingError("LKG_PREVIOUS_CONTENT_INVALID")
        if type(self.current_capture_policy) is not CapturePolicyDecision:
            raise ContentHashingError("LKG_CAPTURE_POLICY_INVALID")
        if (
            self.current_failure_category is not None
            and type(self.current_failure_category) is not FetchFailureCategory
        ):
            raise ContentHashingError("LKG_FAILURE_CATEGORY_INVALID")
        if type(self.governance_allowed) is not bool:
            raise ContentHashingError("LKG_GOVERNANCE_ALLOWED_BOOL_REQUIRED")
        if type(self.identity_reconciled) is not bool:
            raise ContentHashingError("LKG_IDENTITY_RECONCILED_BOOL_REQUIRED")
        if self.freshness_policy is not None and type(
            self.freshness_policy
        ) is not ApprovedLkgFreshnessPolicy:
            raise ContentHashingError("LKG_FRESHNESS_POLICY_INVALID")
        if self.current_attempt_at is not None:
            if type(self.current_attempt_at) is not datetime:
                raise ContentHashingError("LKG_ATTEMPT_TIMESTAMP_INVALID")
            if self.current_attempt_at.utcoffset() is None:
                raise ContentHashingError("LKG_ATTEMPT_TIMESTAMP_AWARE_REQUIRED")

    def __repr__(self) -> str:
        previous_status = (
            self.previous_success.capture_status.value
            if self.previous_success is not None
            else None
        )
        return (
            "LkgEligibilityInput("
            "current_canonical_url=<redacted>, "
            f"previous_status={previous_status!r}, "
            f"capture_mode={self.current_capture_policy.mode.value!r}, "
            f"failure_category={self.current_failure_category!r}, "
            f"governance_allowed={self.governance_allowed!r}, "
            f"identity_reconciled={self.identity_reconciled!r}, "
            f"freshness_policy_configured={self.freshness_policy is not None!r}, "
            f"current_attempt_at={self.current_attempt_at!r})"
        )


class LkgEligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    POLICY_NOT_FULL_TEXT = "policy_not_full_text"
    GOVERNANCE_REJECTED = "governance_rejected"
    IDENTITY_RECONCILIATION_FAILED = "identity_reconciliation_failed"
    NO_PREVIOUS_SUCCESS = "no_previous_success"
    URL_CHANGED = "url_changed"
    FAILURE_NOT_TEMPORARY = "failure_not_temporary"
    FRESHNESS_POLICY_MISSING = "freshness_policy_missing"
    TIMESTAMP_MISSING = "timestamp_missing"
    FRESHNESS_EXPIRED = "freshness_expired"


@dataclass(frozen=True, repr=False)
class _LkgEligibilityBinding:
    """Evaluator-issued context required to compose one exact LKG candidate."""

    previous_captured_content_id: CapturedContentId
    previous_content_hash: str
    previous_parser_version: str
    previous_canonical_url: CanonicalURL
    previous_captured_at: datetime
    previous_last_successful_capture_at: datetime
    previous_previous_content_hash: Optional[str]
    previous_searchable: bool
    current_canonical_url: CanonicalURL
    current_capture_policy: CapturePolicyDecision
    current_failure_category: FetchFailureCategory
    governance_allowed: bool
    identity_reconciled: bool
    freshness_policy: ApprovedLkgFreshnessPolicy
    current_attempt_at: datetime

    def __repr__(self) -> str:
        return "_LkgEligibilityBinding(<redacted>)"


@dataclass(frozen=True, repr=False)
class LkgEligibilityResult:
    eligible: bool
    reason: LkgEligibilityReason
    freshness_policy_version: Optional[str]
    _binding: Optional[_LkgEligibilityBinding] = None

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool or type(self.reason) is not LkgEligibilityReason:
            raise ContentHashingError("LKG_ELIGIBILITY_RESULT_INVALID")
        if self.eligible != (self.reason is LkgEligibilityReason.ELIGIBLE):
            raise ContentHashingError("LKG_ELIGIBILITY_RESULT_INVALID")
        if self.freshness_policy_version is not None:
            _validate_text(
                self.freshness_policy_version,
                "LKG_FRESHNESS_POLICY_VERSION_INVALID",
            )
        if self.eligible and self.freshness_policy_version is None:
            raise ContentHashingError("LKG_ELIGIBILITY_RESULT_INVALID")
        if self.eligible:
            if type(self._binding) is not _LkgEligibilityBinding:
                raise ContentHashingError("LKG_ELIGIBILITY_BINDING_REQUIRED")
            if (
                self.freshness_policy_version
                != self._binding.freshness_policy.policy_version
            ):
                raise ContentHashingError("LKG_ELIGIBILITY_BINDING_MISMATCH")
        elif self._binding is not None:
            raise ContentHashingError("LKG_ELIGIBILITY_BINDING_NOT_ALLOWED")

    def __repr__(self) -> str:
        return (
            "LkgEligibilityResult("
            f"eligible={self.eligible!r}, "
            f"reason={self.reason!r}, "
            "freshness_policy_version="
            f"{'<redacted>' if self.freshness_policy_version is not None else None}, "
            f"binding_attached={self._binding is not None!r})"
        )


def evaluate_lkg_reuse(input_value: LkgEligibilityInput) -> LkgEligibilityResult:
    """Evaluate the frozen LKG gates in their approved order."""

    if type(input_value) is not LkgEligibilityInput:
        raise ContentHashingError("LKG_ELIGIBILITY_INPUT_REQUIRED")

    previous = input_value.previous_success
    current_attempt = input_value.current_attempt_at
    if (
        previous is not None
        and previous.capture_status is CaptureStatus.SUCCESS
        and previous.last_successful_capture_at is not None
        and current_attempt is not None
        and current_attempt < previous.last_successful_capture_at
    ):
        raise ContentHashingError("LKG_TIME_ORDER_INVALID")

    if input_value.current_capture_policy.mode is not CaptureMode.FULL_TEXT:
        return _lkg_result(input_value, LkgEligibilityReason.POLICY_NOT_FULL_TEXT)
    if not input_value.governance_allowed:
        return _lkg_result(input_value, LkgEligibilityReason.GOVERNANCE_REJECTED)
    if not input_value.identity_reconciled:
        return _lkg_result(
            input_value,
            LkgEligibilityReason.IDENTITY_RECONCILIATION_FAILED,
        )
    if previous is None or previous.capture_status is not CaptureStatus.SUCCESS:
        return _lkg_result(input_value, LkgEligibilityReason.NO_PREVIOUS_SUCCESS)
    if input_value.current_canonical_url != previous.canonical_url:
        return _lkg_result(input_value, LkgEligibilityReason.URL_CHANGED)
    if input_value.current_failure_category is not FetchFailureCategory.TEMPORARY:
        return _lkg_result(input_value, LkgEligibilityReason.FAILURE_NOT_TEMPORARY)
    if input_value.freshness_policy is None:
        return _lkg_result(input_value, LkgEligibilityReason.FRESHNESS_POLICY_MISSING)
    if current_attempt is None or previous.last_successful_capture_at is None:
        return _lkg_result(input_value, LkgEligibilityReason.TIMESTAMP_MISSING)
    age = current_attempt - previous.last_successful_capture_at
    if age > input_value.freshness_policy.max_age:
        return _lkg_result(input_value, LkgEligibilityReason.FRESHNESS_EXPIRED)
    return _lkg_result(input_value, LkgEligibilityReason.ELIGIBLE)


def _lkg_result(
    input_value: LkgEligibilityInput,
    reason: LkgEligibilityReason,
) -> LkgEligibilityResult:
    freshness_version = (
        input_value.freshness_policy.policy_version
        if input_value.freshness_policy is not None
        else None
    )
    return LkgEligibilityResult(
        eligible=reason is LkgEligibilityReason.ELIGIBLE,
        reason=reason,
        freshness_policy_version=freshness_version,
        _binding=(
            _build_lkg_eligibility_binding(input_value)
            if reason is LkgEligibilityReason.ELIGIBLE
            else None
        ),
    )


def _build_lkg_eligibility_binding(
    input_value: LkgEligibilityInput,
) -> _LkgEligibilityBinding:
    """Extract the exact composition context without evaluating LKG gates."""

    previous = input_value.previous_success
    freshness_policy = input_value.freshness_policy
    failure_category = input_value.current_failure_category
    current_attempt = input_value.current_attempt_at
    if (
        previous is None
        or previous.capture_status is not CaptureStatus.SUCCESS
        or previous.content_hash is None
        or previous.parser_version is None
        or previous.captured_at is None
        or previous.last_successful_capture_at is None
        or freshness_policy is None
        or failure_category is None
        or current_attempt is None
    ):
        raise ContentHashingError("LKG_ELIGIBILITY_BINDING_INPUT_INVALID")

    return _LkgEligibilityBinding(
        previous_captured_content_id=previous.captured_content_id,
        previous_content_hash=previous.content_hash,
        previous_parser_version=previous.parser_version,
        previous_canonical_url=previous.canonical_url,
        previous_captured_at=previous.captured_at,
        previous_last_successful_capture_at=previous.last_successful_capture_at,
        previous_previous_content_hash=previous.previous_content_hash,
        previous_searchable=previous.searchable,
        current_canonical_url=input_value.current_canonical_url,
        current_capture_policy=input_value.current_capture_policy,
        current_failure_category=failure_category,
        governance_allowed=input_value.governance_allowed,
        identity_reconciled=input_value.identity_reconciled,
        freshness_policy=freshness_policy,
        current_attempt_at=current_attempt,
    )


@dataclass(frozen=True)
class StaleLkgCandidate:
    revision_ref: CaptureRevisionRef
    capture_status: CaptureStatus
    captured_at: datetime
    last_successful_capture_at: datetime
    last_capture_attempt_at: datetime
    previous_content_hash: Optional[str]
    searchable: bool
    freshness_policy_version: str

    def __post_init__(self) -> None:
        if type(self.revision_ref) is not CaptureRevisionRef:
            raise ContentHashingError("STALE_LKG_REVISION_REF_INVALID")
        if self.capture_status is not CaptureStatus.STALE:
            raise ContentHashingError("STALE_LKG_CAPTURE_STATUS_INVALID")
        for value in (
            self.captured_at,
            self.last_successful_capture_at,
            self.last_capture_attempt_at,
        ):
            if type(value) is not datetime or value.utcoffset() is None:
                raise ContentHashingError("STALE_LKG_TIMESTAMP_INVALID")
        if not (
            self.captured_at
            <= self.last_successful_capture_at
            <= self.last_capture_attempt_at
        ):
            raise ContentHashingError("STALE_LKG_TIMESTAMP_INVALID")
        if self.previous_content_hash is not None:
            _validate_text(
                self.previous_content_hash,
                "STALE_LKG_PREVIOUS_HASH_INVALID",
            )
        if type(self.searchable) is not bool:
            raise ContentHashingError("STALE_LKG_SEARCHABLE_BOOL_REQUIRED")
        _validate_text(
            self.freshness_policy_version,
            "LKG_FRESHNESS_POLICY_VERSION_INVALID",
        )


def compose_stale_lkg(
    input_value: LkgEligibilityInput,
    eligibility_result: LkgEligibilityResult,
) -> StaleLkgCandidate:
    """Reference an eligible previous revision without copying its body."""

    if type(input_value) is not LkgEligibilityInput:
        raise ContentHashingError("LKG_ELIGIBILITY_INPUT_REQUIRED")
    if type(eligibility_result) is not LkgEligibilityResult:
        raise ContentHashingError("LKG_ELIGIBILITY_RESULT_REQUIRED")
    if (
        not eligibility_result.eligible
        or eligibility_result.reason is not LkgEligibilityReason.ELIGIBLE
    ):
        raise ContentHashingError("LKG_RESULT_NOT_ELIGIBLE")

    binding = eligibility_result._binding
    if type(binding) is not _LkgEligibilityBinding:
        raise ContentHashingError("LKG_ELIGIBILITY_BINDING_REQUIRED")
    expected_binding = _build_lkg_eligibility_binding(input_value)
    if binding != expected_binding:
        raise ContentHashingError("LKG_ELIGIBILITY_BINDING_MISMATCH")

    return StaleLkgCandidate(
        revision_ref=CaptureRevisionRef(
            captured_content_id=binding.previous_captured_content_id,
            content_hash=CaptureContentHash(binding.previous_content_hash),
            parser_version=binding.previous_parser_version,
        ),
        capture_status=CaptureStatus.STALE,
        captured_at=binding.previous_captured_at,
        last_successful_capture_at=binding.previous_last_successful_capture_at,
        last_capture_attempt_at=binding.current_attempt_at,
        previous_content_hash=binding.previous_previous_content_hash,
        searchable=binding.previous_searchable,
        freshness_policy_version=binding.freshness_policy.policy_version,
    )


def _is_capture_hash(value: str) -> bool:
    if not value.startswith(_SHA256_PREFIX):
        return False
    digest = value[len(_SHA256_PREFIX) :]
    return len(digest) == _SHA256_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in digest
    )


def _validate_text(value: object, error_code: str) -> None:
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ContentHashingError(error_code)


__all__ = [
    "ApprovedLkgFreshnessPolicy",
    "CaptureContentHash",
    "CaptureRevisionRef",
    "ContentHashingError",
    "LkgEligibilityInput",
    "LkgEligibilityReason",
    "LkgEligibilityResult",
    "RevisionDecision",
    "RevisionDisposition",
    "RevisionReason",
    "StaleLkgCandidate",
    "compose_stale_lkg",
    "compute_capture_content_hash",
    "decide_capture_revision",
    "evaluate_lkg_reuse",
]
