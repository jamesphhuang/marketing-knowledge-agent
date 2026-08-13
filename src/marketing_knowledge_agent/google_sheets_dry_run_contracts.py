"""Payload-safe contracts for the Sprint 1 WP2 dry-run boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Dict, Tuple, TYPE_CHECKING

from .google_sheets_read_contracts import ConfiguredReadResult

if TYPE_CHECKING:
    from .google_sheets_source_health import SourceHealthEnvelope


FIRST_LIVE_BASELINE_SCHEMA_VERSION = (
    "s1-wp2-first-live-baseline-evidence-v1"
)
EVIDENCE_HASH_DOMAIN = b"first-live-baseline-evidence:v1\x00"
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_STRUCTURAL_REASON_CODE_ALLOWLIST = frozenset(
    {
        "SOURCE_HEALTH_CRITICAL_SHEET_PROFILE_MISMATCH",
        "SOURCE_HEALTH_CRITICAL_SHEET_SET_MISMATCH",
        "SOURCE_HEALTH_HANDLE_MAPPING_HEADER_MISMATCH",
        "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
        "SOURCE_HEALTH_PENDING_POSITIONAL_BINDING_MISMATCH",
        "SOURCE_HEALTH_PUBLIC_METRIC_HEADER_MISMATCH",
        "SOURCE_HEALTH_RESTRICTED_CUSTOMER_HEADER_MISMATCH",
    }
)
_DEFERRED_CHECK_CODE_ALLOWLIST = frozenset(
    {
        "BRAND_ID_INITIAL_REVIEW_DEFERRED",
        "BRAND_ID_MAPPING_AUTHORITY_DEFERRED",
        "MERCHANT_BRD_ASSIGNMENT_DEFERRED",
        "MERCHANT_ID_REVIEW_STATUS_DEFERRED",
        "MERCHANT_MREC_ASSIGNMENT_DEFERRED",
        "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED",
    }
)


class DryRunContractError(ValueError):
    """Stable payload-free WP2 contract failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class RunMode(str, Enum):
    FIRST_LIVE = "FIRST_LIVE"
    SYNTHETIC = "SYNTHETIC"


class SourceHealthDisposition(str, Enum):
    STRUCTURAL_BLOCK = "STRUCTURAL_BLOCK"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    SYNTHETIC_CHECKS_COMPLETE = "SYNTHETIC_CHECKS_COMPLETE"


@dataclass(frozen=True)
class SafeStructuralCounts:
    """The complete WP2 v1 allowlist of non-sensitive count facts."""

    configured_range_count: int
    covered_range_count: int
    configured_sheet_count: int
    observed_sheet_count: int
    critical_sheet_expected_count: int
    critical_sheet_observed_count: int
    header_binding_expected_count: int
    header_binding_valid_count: int
    positional_binding_expected_count: int
    positional_binding_valid_count: int
    structural_issue_count: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in self.__dict__.values()):
            _fail("SAFE_STRUCTURAL_COUNT_INVALID")
        if (
            self.configured_range_count != 5
            or self.covered_range_count > self.configured_range_count
            or self.configured_sheet_count != 5
            or self.observed_sheet_count > self.configured_sheet_count
            or self.critical_sheet_expected_count != 5
            or self.critical_sheet_observed_count
            > self.critical_sheet_expected_count
            or self.critical_sheet_observed_count > self.observed_sheet_count
            or self.header_binding_expected_count != 4
            or self.header_binding_valid_count > self.header_binding_expected_count
            or self.positional_binding_expected_count != 1
            or self.positional_binding_valid_count
            > self.positional_binding_expected_count
            or self.structural_issue_count > len(_STRUCTURAL_REASON_CODE_ALLOWLIST)
        ):
            _fail("SAFE_STRUCTURAL_COUNT_RECONCILIATION_FAILED")

    def as_dict(self) -> Dict[str, int]:
        return {
            "configured_range_count": self.configured_range_count,
            "covered_range_count": self.covered_range_count,
            "configured_sheet_count": self.configured_sheet_count,
            "observed_sheet_count": self.observed_sheet_count,
            "critical_sheet_expected_count": self.critical_sheet_expected_count,
            "critical_sheet_observed_count": self.critical_sheet_observed_count,
            "header_binding_expected_count": self.header_binding_expected_count,
            "header_binding_valid_count": self.header_binding_valid_count,
            "positional_binding_expected_count": self.positional_binding_expected_count,
            "positional_binding_valid_count": self.positional_binding_valid_count,
            "structural_issue_count": self.structural_issue_count,
        }


class CoverageProvenBatchContext:
    """Opaque in-memory coupling of one configured read and its WP2 envelope."""

    __slots__ = (
        "_configured_read_result",
        "_envelope",
        "_result_object_identity",
        "_run_provenance",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "CoverageProvenBatchContext":
        raise TypeError("COVERAGE_PROVEN_CONTEXT_CONSTRUCTION_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("COVERAGE_PROVEN_CONTEXT_IMMUTABLE")

    def __repr__(self) -> str:
        return "CoverageProvenBatchContext(<sensitive>)"

    def __reduce__(self) -> object:
        raise TypeError("COVERAGE_PROVEN_CONTEXT_PICKLE_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("COVERAGE_PROVEN_CONTEXT_PICKLE_FORBIDDEN")


class _RunModeProvenance:
    """Opaque builder-issued mode binding for one exact result and envelope."""

    __slots__ = (
        "_authority",
        "_result_object_identity",
        "_envelope_object_identity",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "_RunModeProvenance":
        raise TypeError("RUN_MODE_PROVENANCE_CONSTRUCTION_FORBIDDEN")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("RUN_MODE_PROVENANCE_IMMUTABLE")

    def __repr__(self) -> str:
        return "_RunModeProvenance(<opaque>)"

    def __reduce__(self) -> object:
        raise TypeError("RUN_MODE_PROVENANCE_PICKLE_FORBIDDEN")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("RUN_MODE_PROVENANCE_PICKLE_FORBIDDEN")


_FIRST_LIVE_RUN_AUTHORITY = object()
_SYNTHETIC_RUN_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class FirstLiveBaselineEvidence:
    """Versioned, redacted evidence for human first-live baseline review."""

    schema_version: str
    evidence_kind: str
    authority: str
    review_scope: str
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
    structural_reason_codes: Tuple[str, ...]
    deferred_check_codes: Tuple[str, ...]
    disposition: SourceHealthDisposition
    evidence_hash: str

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("FIRST_LIVE_EVIDENCE_CONSTRUCTION_FORBIDDEN")

    def canonical_mapping(self) -> Dict[str, object]:
        return _evidence_primitive(self, include_hash=True)

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_mapping()).decode("utf-8")

    def __repr__(self) -> str:
        return (
            "FirstLiveBaselineEvidence("
            f"disposition={self.disposition.value!r}, "
            f"evidence_hash={self.evidence_hash!r})"
        )


def create_first_live_baseline_evidence(
    context: CoverageProvenBatchContext,
) -> FirstLiveBaselineEvidence:
    """Project one opaque context into the reviewed redacted evidence contract."""

    from .google_sheets_source_health import _validated_context_envelope

    envelope = _validated_context_envelope(context)
    if envelope is None:
        _fail("COVERAGE_PROVEN_CONTEXT_BINDING_INVALID")
    trusted_mode = _context_run_provenance_mode(context)
    if (
        trusted_mode is not RunMode.FIRST_LIVE
        or envelope.run_mode is not RunMode.FIRST_LIVE
    ):
        _fail("FIRST_LIVE_EVIDENCE_FIRST_LIVE_CONTEXT_REQUIRED")
    evidence = _new_evidence(
        schema_version=FIRST_LIVE_BASELINE_SCHEMA_VERSION,
        evidence_kind="FIRST_LIVE_BASELINE",
        authority="NON_AUTHORITATIVE",
        review_scope="HUMAN_BASELINE_REVIEW_ONLY",
        target_identity_hash=envelope.target_identity_hash,
        configuration_identity=envelope.configuration_identity,
        config_version=envelope.config_version,
        coverage_identity=envelope.coverage_identity,
        mapper_version=envelope.mapper_version,
        snapshot_schema_version=envelope.snapshot_schema_version,
        fingerprint_semantics_version=envelope.fingerprint_semantics_version,
        source_health_rules_version=envelope.source_health_rules_version,
        source_fingerprint=envelope.source_fingerprint,
        safe_counts=envelope.safe_counts,
        structural_reason_codes=envelope.structural_reason_codes,
        deferred_check_codes=envelope.deferred_check_codes,
        disposition=envelope.disposition,
        evidence_hash="",
    )
    object.__setattr__(evidence, "evidence_hash", compute_evidence_hash(evidence))
    _validate_evidence(evidence)
    return evidence


def compute_evidence_hash(evidence: FirstLiveBaselineEvidence) -> str:
    if type(evidence) is not FirstLiveBaselineEvidence:
        _fail("FIRST_LIVE_EVIDENCE_TYPE_INVALID")
    payload = _canonical_json(_evidence_primitive(evidence, include_hash=False))
    return "sha256:" + hashlib.sha256(EVIDENCE_HASH_DOMAIN + payload).hexdigest()


def _create_coverage_proven_batch_context(
    configured_read_result: ConfiguredReadResult,
    envelope: "SourceHealthEnvelope",
    run_provenance: object = None,
) -> CoverageProvenBatchContext:
    if type(configured_read_result) is not ConfiguredReadResult:
        _fail("COVERAGE_PROVEN_CONTEXT_RESULT_INVALID")
    if getattr(envelope, "_result_object_identity", None) != id(
        configured_read_result
    ):
        _fail("COVERAGE_PROVEN_CONTEXT_BINDING_MISMATCH")
    if (
        _provenance_mode_if_valid(
            run_provenance, configured_read_result, envelope
        )
        is None
    ):
        _fail("COVERAGE_PROVEN_CONTEXT_PROVENANCE_INVALID")
    context = object.__new__(CoverageProvenBatchContext)
    object.__setattr__(context, "_configured_read_result", configured_read_result)
    object.__setattr__(context, "_envelope", envelope)
    object.__setattr__(context, "_result_object_identity", id(configured_read_result))
    object.__setattr__(context, "_run_provenance", run_provenance)
    return context


def _context_binding_is_valid(value: object) -> bool:
    if type(value) is not CoverageProvenBatchContext:
        return False
    try:
        result = object.__getattribute__(value, "_configured_read_result")
        envelope = object.__getattribute__(value, "_envelope")
        identity = object.__getattribute__(value, "_result_object_identity")
        run_provenance = object.__getattribute__(value, "_run_provenance")
    except (AttributeError, TypeError):
        return False
    trusted_mode = _provenance_mode_if_valid(run_provenance, result, envelope)
    return (
        type(result) is ConfiguredReadResult
        and identity == id(result)
        and getattr(envelope, "_result_object_identity", None) == identity
        and trusted_mode is not None
        and getattr(envelope, "run_mode", None) is trusted_mode
    )


def _context_result(context: CoverageProvenBatchContext) -> ConfiguredReadResult:
    return object.__getattribute__(context, "_configured_read_result")


def _context_envelope(context: CoverageProvenBatchContext) -> "SourceHealthEnvelope":
    return object.__getattribute__(context, "_envelope")


def _context_run_provenance_mode(
    context: CoverageProvenBatchContext,
) -> object:
    try:
        result = object.__getattribute__(context, "_configured_read_result")
        envelope = object.__getattribute__(context, "_envelope")
        provenance = object.__getattribute__(context, "_run_provenance")
    except (AttributeError, TypeError):
        return None
    return _provenance_mode_if_valid(provenance, result, envelope)


def _issue_first_live_run_provenance(
    configured_read_result: ConfiguredReadResult,
    envelope: "SourceHealthEnvelope",
) -> _RunModeProvenance:
    return _new_run_mode_provenance(
        _FIRST_LIVE_RUN_AUTHORITY, configured_read_result, envelope
    )


def _issue_synthetic_run_provenance(
    configured_read_result: ConfiguredReadResult,
    envelope: "SourceHealthEnvelope",
) -> _RunModeProvenance:
    return _new_run_mode_provenance(
        _SYNTHETIC_RUN_AUTHORITY, configured_read_result, envelope
    )


def _new_run_mode_provenance(
    authority: object,
    configured_read_result: ConfiguredReadResult,
    envelope: "SourceHealthEnvelope",
) -> _RunModeProvenance:
    provenance = object.__new__(_RunModeProvenance)
    object.__setattr__(provenance, "_authority", authority)
    object.__setattr__(
        provenance, "_result_object_identity", id(configured_read_result)
    )
    object.__setattr__(provenance, "_envelope_object_identity", id(envelope))
    return provenance


def _provenance_mode_if_valid(
    provenance: object,
    configured_read_result: object,
    envelope: object,
) -> object:
    if type(provenance) is not _RunModeProvenance:
        return None
    try:
        authority = object.__getattribute__(provenance, "_authority")
        result_identity = object.__getattribute__(
            provenance, "_result_object_identity"
        )
        envelope_identity = object.__getattribute__(
            provenance, "_envelope_object_identity"
        )
    except (AttributeError, TypeError):
        return None
    if (
        result_identity != id(configured_read_result)
        or envelope_identity != id(envelope)
    ):
        return None
    if authority is _FIRST_LIVE_RUN_AUTHORITY:
        return RunMode.FIRST_LIVE
    if authority is _SYNTHETIC_RUN_AUTHORITY:
        return RunMode.SYNTHETIC
    return None


def _new_evidence(**fields: object) -> FirstLiveBaselineEvidence:
    evidence = object.__new__(FirstLiveBaselineEvidence)
    for name in _evidence_field_names():
        object.__setattr__(evidence, name, fields[name])
    return evidence


def _validate_evidence(evidence: FirstLiveBaselineEvidence) -> None:
    fixed = {
        "schema_version": FIRST_LIVE_BASELINE_SCHEMA_VERSION,
        "evidence_kind": "FIRST_LIVE_BASELINE",
        "authority": "NON_AUTHORITATIVE",
        "review_scope": "HUMAN_BASELINE_REVIEW_ONLY",
    }
    for field_name, expected in fixed.items():
        if getattr(evidence, field_name) != expected:
            _fail("FIRST_LIVE_EVIDENCE_FIXED_FIELD_INVALID")
    string_fields = (
        "target_identity_hash",
        "configuration_identity",
        "config_version",
        "coverage_identity",
        "mapper_version",
        "snapshot_schema_version",
        "fingerprint_semantics_version",
        "source_health_rules_version",
        "source_fingerprint",
    )
    if any(not isinstance(getattr(evidence, name), str) for name in string_fields):
        _fail("FIRST_LIVE_EVIDENCE_VALUE_INVALID")
    for name in (
        "target_identity_hash",
        "configuration_identity",
        "coverage_identity",
        "source_fingerprint",
        "evidence_hash",
    ):
        if not _HASH_PATTERN.fullmatch(getattr(evidence, name)):
            _fail("FIRST_LIVE_EVIDENCE_HASH_INVALID")
    if type(evidence.safe_counts) is not SafeStructuralCounts:
        _fail("FIRST_LIVE_EVIDENCE_SAFE_COUNTS_INVALID")
    structural_codes = _canonical_structural_reason_codes(
        evidence.structural_reason_codes
    )
    deferred_codes = _canonical_deferred_check_codes(evidence.deferred_check_codes)
    if structural_codes != evidence.structural_reason_codes:
        _fail("FIRST_LIVE_EVIDENCE_STRUCTURAL_CODES_NOT_CANONICAL")
    if deferred_codes != evidence.deferred_check_codes:
        _fail("FIRST_LIVE_EVIDENCE_DEFERRED_CODES_NOT_CANONICAL")
    if evidence.safe_counts.structural_issue_count != len(
        evidence.structural_reason_codes
    ):
        _fail("FIRST_LIVE_EVIDENCE_COUNT_RECONCILIATION_FAILED")
    if type(evidence.disposition) is not SourceHealthDisposition:
        _fail("FIRST_LIVE_EVIDENCE_DISPOSITION_INVALID")
    if evidence.evidence_hash != compute_evidence_hash(evidence):
        _fail("FIRST_LIVE_EVIDENCE_HASH_MISMATCH")


def _evidence_primitive(
    evidence: FirstLiveBaselineEvidence, *, include_hash: bool
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "schema_version": evidence.schema_version,
        "evidence_kind": evidence.evidence_kind,
        "authority": evidence.authority,
        "review_scope": evidence.review_scope,
        "target_identity_hash": evidence.target_identity_hash,
        "configuration_identity": evidence.configuration_identity,
        "config_version": evidence.config_version,
        "coverage_identity": evidence.coverage_identity,
        "mapper_version": evidence.mapper_version,
        "snapshot_schema_version": evidence.snapshot_schema_version,
        "fingerprint_semantics_version": evidence.fingerprint_semantics_version,
        "source_health_rules_version": evidence.source_health_rules_version,
        "source_fingerprint": evidence.source_fingerprint,
        "safe_counts": evidence.safe_counts.as_dict(),
        "structural_reason_codes": list(evidence.structural_reason_codes),
        "deferred_check_codes": list(evidence.deferred_check_codes),
        "disposition": evidence.disposition.value,
    }
    if include_hash:
        payload["evidence_hash"] = evidence.evidence_hash
    return payload


def _canonical_json(value: object) -> bytes:
    serialized = None
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        pass
    if serialized is None:
        _fail("FIRST_LIVE_EVIDENCE_NOT_CANONICAL")
    return serialized


def _canonical_structural_reason_codes(value: object) -> Tuple[str, ...]:
    return _canonical_codes(
        value,
        _STRUCTURAL_REASON_CODE_ALLOWLIST,
        "STRUCTURAL_REASON_CODE_INVALID",
    )


def _canonical_deferred_check_codes(value: object) -> Tuple[str, ...]:
    return _canonical_codes(
        value,
        _DEFERRED_CHECK_CODE_ALLOWLIST,
        "DEFERRED_CHECK_CODE_INVALID",
    )


def _canonical_codes(
    value: object,
    allowlist: frozenset,
    error_code: str,
) -> Tuple[str, ...]:
    if type(value) not in {tuple, list}:
        _fail(error_code)
    copied = tuple(value)
    if (
        any(type(code) is not str or code not in allowlist for code in copied)
        or len(copied) != len(set(copied))
    ):
        _fail(error_code)
    return tuple(sorted(copied))


def _evidence_field_names() -> Tuple[str, ...]:
    return tuple(FirstLiveBaselineEvidence.__dataclass_fields__)


def _fail(code: str) -> None:
    raise DryRunContractError(code) from None


__all__ = [
    "CoverageProvenBatchContext",
    "DryRunContractError",
    "FirstLiveBaselineEvidence",
    "RunMode",
    "SafeStructuralCounts",
    "SourceHealthDisposition",
    "compute_evidence_hash",
    "create_first_live_baseline_evidence",
]
