"""Pure comparison of two already coverage-proven WP2 fingerprints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .google_sheets_dry_run_contracts import CoverageProvenBatchContext
from .google_sheets_source_health import (
    _context_coverage_binding_valid,
    _context_facts,
)


class FingerprintComparisonOutcome(str, Enum):
    EQUAL = "EQUAL"
    DIFFERENT = "DIFFERENT"
    NOT_COMPARABLE = "NOT_COMPARABLE"


@dataclass(frozen=True)
class FingerprintComparisonResult:
    outcome: FingerprintComparisonOutcome
    mismatch_code: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, FingerprintComparisonOutcome):
            raise ValueError("F1_COMPARISON_OUTCOME_INVALID")
        if self.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE:
            if not isinstance(self.mismatch_code, str) or not self.mismatch_code:
                raise ValueError("F1_COMPARISON_MISMATCH_CODE_REQUIRED")
        elif self.mismatch_code is not None:
            raise ValueError("F1_COMPARISON_MISMATCH_CODE_UNEXPECTED")


def compare_coverage_proven_fingerprints(
    first_context: CoverageProvenBatchContext,
    second_context: CoverageProvenBatchContext,
) -> FingerprintComparisonResult:
    """Compare F1 values without acquiring or persisting either source."""

    if not _context_coverage_binding_valid(first_context):
        return _not_comparable("F1_COMPARE_FIRST_COVERAGE_INVALID")
    if not _context_coverage_binding_valid(second_context):
        return _not_comparable("F1_COMPARE_SECOND_COVERAGE_INVALID")

    first = _context_facts(first_context)
    second = _context_facts(second_context)
    mismatch_checks = (
        (0, "F1_COMPARE_TARGET_MISMATCH"),
        (1, "F1_COMPARE_CONFIG_MISMATCH"),
        (2, "F1_COMPARE_COVERAGE_MISMATCH"),
        (3, "F1_COMPARE_MAPPER_VERSION_MISMATCH"),
        (4, "F1_COMPARE_SNAPSHOT_SCHEMA_VERSION_MISMATCH"),
        (5, "F1_COMPARE_FINGERPRINT_VERSION_MISMATCH"),
    )
    for index, code in mismatch_checks:
        if first[index] != second[index]:
            return _not_comparable(code)
    outcome = (
        FingerprintComparisonOutcome.EQUAL
        if first[6] == second[6]
        else FingerprintComparisonOutcome.DIFFERENT
    )
    return FingerprintComparisonResult(outcome=outcome)


def _not_comparable(code: str) -> FingerprintComparisonResult:
    return FingerprintComparisonResult(
        outcome=FingerprintComparisonOutcome.NOT_COMPARABLE,
        mismatch_code=code,
    )


__all__ = [
    "FingerprintComparisonOutcome",
    "FingerprintComparisonResult",
    "compare_coverage_proven_fingerprints",
]
