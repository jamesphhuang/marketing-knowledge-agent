"""Payload-safe contracts for the Sprint 1 WP2 dry-run boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Dict, Mapping, Tuple, TYPE_CHECKING

from .google_sheets_read_contracts import ConfiguredReadResult

if TYPE_CHECKING:
    from .google_sheets_source_health import SourceHealthEnvelope


FIRST_LIVE_BASELINE_SCHEMA_VERSION = (
    "s1-wp2-first-live-baseline-evidence-v1"
)
EVIDENCE_HASH_DOMAIN = b"first-live-baseline-evidence:v1\x00"
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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

    __slots__ = ("_configured_read_result", "_envelope", "_result_object_identity")

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

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FirstLiveBaselineEvidence":
        """Parse only the exact reviewed evidence field allowlist."""

        if not isinstance(value, Mapping):
            _fail("FIRST_LIVE_EVIDENCE_TYPE_INVALID")
        expected = set(_evidence_field_names())
        if set(value) != expected:
            _fail("FIRST_LIVE_EVIDENCE_FIELDS_INVALID")
        safe_counts_value = value["safe_counts"]
        if not isinstance(safe_counts_value, Mapping):
            _fail("FIRST_LIVE_EVIDENCE_SAFE_COUNTS_INVALID")
        safe_count_fields = set(SafeStructuralCounts.__dataclass_fields__)
        if set(safe_counts_value) != safe_count_fields:
            _fail("FIRST_LIVE_EVIDENCE_SAFE_COUNTS_INVALID")
        try:
            safe_counts = SafeStructuralCounts(
                **{key: safe_counts_value[key] for key in safe_count_fields}
            )
            disposition = SourceHealthDisposition(value["disposition"])
            evidence = _new_evidence(
                schema_version=value["schema_version"],
                evidence_kind=value["evidence_kind"],
                authority=value["authority"],
                review_scope=value["review_scope"],
                target_identity_hash=value["target_identity_hash"],
                configuration_identity=value["configuration_identity"],
                config_version=value["config_version"],
                coverage_identity=value["coverage_identity"],
                mapper_version=value["mapper_version"],
                snapshot_schema_version=value["snapshot_schema_version"],
                fingerprint_semantics_version=value[
                    "fingerprint_semantics_version"
                ],
                source_health_rules_version=value["source_health_rules_version"],
                source_fingerprint=value["source_fingerprint"],
                safe_counts=safe_counts,
                structural_reason_codes=_strict_code_tuple(
                    value["structural_reason_codes"]
                ),
                deferred_check_codes=_strict_code_tuple(value["deferred_check_codes"]),
                disposition=disposition,
                evidence_hash=value["evidence_hash"],
            )
        except (KeyError, TypeError, ValueError):
            _fail("FIRST_LIVE_EVIDENCE_VALUE_INVALID")
        _validate_evidence(evidence)
        return evidence

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

    if not _context_binding_is_valid(context):
        _fail("COVERAGE_PROVEN_CONTEXT_BINDING_INVALID")
    envelope = _context_envelope(context)
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
    if not isinstance(evidence, FirstLiveBaselineEvidence):
        _fail("FIRST_LIVE_EVIDENCE_TYPE_INVALID")
    payload = _canonical_json(_evidence_primitive(evidence, include_hash=False))
    return "sha256:" + hashlib.sha256(EVIDENCE_HASH_DOMAIN + payload).hexdigest()


def _create_coverage_proven_batch_context(
    configured_read_result: ConfiguredReadResult,
    envelope: "SourceHealthEnvelope",
) -> CoverageProvenBatchContext:
    if not isinstance(configured_read_result, ConfiguredReadResult):
        _fail("COVERAGE_PROVEN_CONTEXT_RESULT_INVALID")
    if getattr(envelope, "_result_object_identity", None) != id(
        configured_read_result
    ):
        _fail("COVERAGE_PROVEN_CONTEXT_BINDING_MISMATCH")
    context = object.__new__(CoverageProvenBatchContext)
    object.__setattr__(context, "_configured_read_result", configured_read_result)
    object.__setattr__(context, "_envelope", envelope)
    object.__setattr__(context, "_result_object_identity", id(configured_read_result))
    return context


def _context_binding_is_valid(value: object) -> bool:
    if not isinstance(value, CoverageProvenBatchContext):
        return False
    try:
        result = object.__getattribute__(value, "_configured_read_result")
        envelope = object.__getattribute__(value, "_envelope")
        identity = object.__getattribute__(value, "_result_object_identity")
    except (AttributeError, TypeError):
        return False
    return (
        isinstance(result, ConfiguredReadResult)
        and identity == id(result)
        and getattr(envelope, "_result_object_identity", None) == identity
    )


def _context_result(context: CoverageProvenBatchContext) -> ConfiguredReadResult:
    return object.__getattribute__(context, "_configured_read_result")


def _context_envelope(context: CoverageProvenBatchContext) -> "SourceHealthEnvelope":
    return object.__getattribute__(context, "_envelope")


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
    if not isinstance(evidence.safe_counts, SafeStructuralCounts):
        _fail("FIRST_LIVE_EVIDENCE_SAFE_COUNTS_INVALID")
    _validate_codes(evidence.structural_reason_codes)
    _validate_codes(evidence.deferred_check_codes)
    if evidence.safe_counts.structural_issue_count != len(
        evidence.structural_reason_codes
    ):
        _fail("FIRST_LIVE_EVIDENCE_COUNT_RECONCILIATION_FAILED")
    if not isinstance(evidence.disposition, SourceHealthDisposition):
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
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("FIRST_LIVE_EVIDENCE_NOT_CANONICAL")


def _strict_code_tuple(value: object) -> Tuple[str, ...]:
    if type(value) not in {tuple, list}:
        _fail("FIRST_LIVE_EVIDENCE_CODE_LIST_INVALID")
    return tuple(value)


def _validate_codes(value: Tuple[str, ...]) -> None:
    if type(value) is not tuple or any(
        not isinstance(code, str) or not code for code in value
    ):
        _fail("FIRST_LIVE_EVIDENCE_CODE_LIST_INVALID")
    if value != tuple(sorted(value)) or len(value) != len(set(value)):
        _fail("FIRST_LIVE_EVIDENCE_CODE_LIST_INVALID")


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
