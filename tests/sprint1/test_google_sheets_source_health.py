from __future__ import annotations

from dataclasses import replace
import json
import sys

import pytest

from marketing_knowledge_agent.google_sheets_dry_run_contracts import (
    CoverageProvenBatchContext,
    DryRunContractError,
    RunMode,
    SourceHealthDisposition,
    create_first_live_baseline_evidence,
    _context_envelope,
    _context_result,
    _create_coverage_proven_batch_context,
)
from marketing_knowledge_agent.google_sheets_fingerprint_check import (
    FingerprintComparisonOutcome,
    compare_coverage_proven_fingerprints,
)
from marketing_knowledge_agent.google_sheets_source_health import (
    SourceHealthError,
    build_first_live_coverage_proven_batch_context,
    build_synthetic_coverage_proven_batch_context,
    _validated_context_envelope,
)
from sprint1.test_google_sheets_dry_run_contracts import (
    FIXED_UUID4,
    configured_result,
    context,
    context_provenance,
    forged_exact_context,
    production_response,
    synthetic_context,
)


def test_exact_five_sheet_profile_headers_and_pending_positional_binding():
    envelope = _context_envelope(context())
    counts = envelope.safe_counts

    assert counts.configured_sheet_count == 5
    assert counts.observed_sheet_count == 5
    assert counts.critical_sheet_expected_count == 5
    assert counts.critical_sheet_observed_count == 5
    assert counts.header_binding_expected_count == 4
    assert counts.header_binding_valid_count == 4
    assert counts.positional_binding_expected_count == 1
    assert counts.positional_binding_valid_count == 1
    assert counts.configured_range_count == counts.covered_range_count == 5
    assert envelope.structural_reason_codes == ()


def test_pending_first_data_row_is_not_inferred_as_a_header():
    response = production_response()
    pending = next(
        sheet
        for sheet in response["sheets"]
        if sheet["properties"]["title"] == "待確認數據"
    )
    pending["data"][0]["rowData"][0]["values"] = [
        {"formattedValue": "類型"},
        {"formattedValue": "指標"},
        {"formattedValue": "論述"},
        {"formattedValue": "備註"},
    ]
    from marketing_knowledge_agent.google_sheets_response_mapper import (
        map_google_sheets_response,
    )
    from marketing_knowledge_agent.google_sheets_runtime_config import (
        production_google_sheets_runtime_config,
    )

    result = map_google_sheets_response(
        response, production_google_sheets_runtime_config().read_plan
    )
    built = build_synthetic_coverage_proven_batch_context(
        result,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    envelope = _context_envelope(built)

    assert envelope.safe_counts.positional_binding_valid_count == 1
    assert envelope.sensitive_occupied_row_counts.pending_metric == 1
    assert envelope.structural_reason_codes == ()


def test_each_source_occupied_row_count_is_sensitive_in_memory_only():
    envelope = _context_envelope(context())
    sensitive = envelope.sensitive_occupied_row_counts
    evidence = create_first_live_baseline_evidence(context())
    serialized = evidence.canonical_json()

    assert sensitive.merchant_case == 1
    assert sensitive.restricted_customer == 1
    assert sensitive.public_metric == 1
    assert sensitive.pending_metric == 1
    assert sensitive.handle_mapping == 1
    assert repr(sensitive) == "SensitiveOccupiedRowCounts(<redacted>)"
    assert not hasattr(sensitive, "as_dict")
    assert not hasattr(sensitive, "to_json")
    assert all(
        source_category not in serialized
        for source_category in (
            "merchant_case",
            "restricted_customer",
            "public_metric",
            "pending_metric",
            "handle_mapping",
        )
    )


def test_deferred_governance_is_explicit_and_not_falsely_counted_as_zero():
    evidence = create_first_live_baseline_evidence(context())
    safe_counts = evidence.safe_counts.as_dict()

    assert evidence.deferred_check_codes == tuple(sorted(evidence.deferred_check_codes))
    assert len(evidence.deferred_check_codes) == len(set(evidence.deferred_check_codes))
    assert {
        "MERCHANT_MREC_ASSIGNMENT_DEFERRED",
        "MERCHANT_BRD_ASSIGNMENT_DEFERRED",
        "MERCHANT_ID_REVIEW_STATUS_DEFERRED",
        "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED",
        "BRAND_ID_MAPPING_AUTHORITY_DEFERRED",
        "BRAND_ID_INITIAL_REVIEW_DEFERRED",
    } == set(evidence.deferred_check_codes)
    assert not any(
        token in key.upper()
        for key in safe_counts
        for token in ("MREC", "MET", "BRD", "ENTITY", "EXCLUSION")
    )


def test_header_drift_blocks_both_synthetic_and_first_live_without_pass():
    result = configured_result(change="header")
    synthetic = build_synthetic_coverage_proven_batch_context(
        result, _correlation_id_factory=lambda: FIXED_UUID4
    )
    first_live = build_first_live_coverage_proven_batch_context(
        result, _correlation_id_factory=lambda: FIXED_UUID4
    )
    for built in (synthetic, first_live):
        envelope = _context_envelope(built)
        assert envelope.disposition is SourceHealthDisposition.STRUCTURAL_BLOCK
        assert envelope.structural_reason_codes == (
            "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
        )
        assert envelope.safe_counts.structural_issue_count == 1
    evidence = create_first_live_baseline_evidence(first_live)
    assert evidence.disposition is SourceHealthDisposition.STRUCTURAL_BLOCK
    assert "PASS" not in evidence.canonical_json()


def test_structural_errors_are_payload_free_even_for_hostile_input():
    class Hostile:
        def __repr__(self):
            return "RAW_CELL_RESTRICTED_CUSTOMER_CREDENTIAL_URL_SENTINEL"

    with pytest.raises(SourceHealthError) as caught:
        build_synthetic_coverage_proven_batch_context(
            Hostile(),
        )

    rendered = repr(caught.value) + str(caught.value)
    assert rendered == (
        "SourceHealthError(code='SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED')"
        "SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED"
    )
    assert "RAW_CELL" not in rendered
    assert "CREDENTIAL" not in rendered


def test_fingerprint_comparison_equal_and_different():
    equal = compare_coverage_proven_fingerprints(context(), context())
    different = compare_coverage_proven_fingerprints(
        context(), context(change="semantic")
    )

    assert equal.outcome is FingerprintComparisonOutcome.EQUAL
    assert equal.mismatch_code is None
    assert different.outcome is FingerprintComparisonOutcome.DIFFERENT
    assert different.mismatch_code is None


@pytest.mark.parametrize(
    ("field_name", "value", "mismatch_code"),
    [
        (
            "target_identity_hash",
            "sha256:" + "1" * 64,
            "F1_COMPARE_TARGET_MISMATCH",
        ),
        (
            "configuration_identity",
            "sha256:" + "2" * 64,
            "F1_COMPARE_CONFIG_MISMATCH",
        ),
        (
            "coverage_identity",
            "sha256:" + "3" * 64,
            "F1_COMPARE_COVERAGE_MISMATCH",
        ),
        (
            "mapper_version",
            "future-mapper-v2",
            "F1_COMPARE_MAPPER_VERSION_MISMATCH",
        ),
        (
            "snapshot_schema_version",
            "future-snapshot-schema-v2",
            "F1_COMPARE_SNAPSHOT_SCHEMA_VERSION_MISMATCH",
        ),
        (
            "fingerprint_semantics_version",
            "future-fingerprint-semantics-v2",
            "F1_COMPARE_FINGERPRINT_VERSION_MISMATCH",
        ),
    ],
)
def test_fingerprint_comparison_reports_each_mismatch_in_order(
    field_name, value, mismatch_code
):
    first = context()
    second = context()
    changed_envelope = replace(_context_envelope(second), **{field_name: value})
    changed_context = forged_exact_context(
        _context_result(second), changed_envelope, context_provenance(second)
    )

    result = compare_coverage_proven_fingerprints(first, changed_context)

    assert result.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert result.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"


def test_fingerprint_comparison_validates_first_then_second_context():
    invalid_first = compare_coverage_proven_fingerprints(object(), object())
    invalid_second = compare_coverage_proven_fingerprints(context(), object())

    assert invalid_first.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert invalid_first.mismatch_code == "F1_COMPARE_FIRST_COVERAGE_INVALID"
    assert invalid_second.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert invalid_second.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"


def test_fingerprint_comparison_checks_identity_before_f1():
    first = context()
    second = context(change="semantic")
    changed_envelope = replace(
        _context_envelope(second), target_identity_hash="sha256:" + "4" * 64
    )
    changed_context = forged_exact_context(
        _context_result(second), changed_envelope, context_provenance(second)
    )

    result = compare_coverage_proven_fingerprints(first, changed_context)

    assert result.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"


def test_fingerprint_comparison_has_no_io_network_or_transport_authority(monkeypatch):
    first = context()
    second = context()
    transport_before = sys.modules.get(
        "marketing_knowledge_agent.google_sheets_transport"
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("comparison helper attempted external authority")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.getaddrinfo", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)

    result = compare_coverage_proven_fingerprints(first, second)

    assert result.outcome is FingerprintComparisonOutcome.EQUAL
    assert sys.modules.get(
        "marketing_knowledge_agent.google_sheets_transport"
    ) is transport_before


def test_safe_evidence_json_is_stable_sorted_and_has_no_unknown_nested_fields():
    evidence = create_first_live_baseline_evidence(context())
    decoded = json.loads(evidence.canonical_json())

    assert evidence.canonical_json() == json.dumps(
        decoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert set(decoded["safe_counts"]) == {
        "configured_range_count",
        "covered_range_count",
        "configured_sheet_count",
        "observed_sheet_count",
        "critical_sheet_expected_count",
        "critical_sheet_observed_count",
        "header_binding_expected_count",
        "header_binding_valid_count",
        "positional_binding_expected_count",
        "positional_binding_valid_count",
        "structural_issue_count",
    }


def test_context_subclass_and_altered_envelope_are_rejected():
    genuine = context()
    result = _context_result(genuine)
    altered = replace(
        _context_envelope(genuine),
        source_fingerprint="sha256:" + "9" * 64,
    )

    class ForgedContext(CoverageProvenBatchContext):
        __slots__ = ()

        def __new__(cls, source_result, source_envelope):
            value = object.__new__(cls)
            object.__setattr__(value, "_configured_read_result", source_result)
            object.__setattr__(value, "_envelope", source_envelope)
            object.__setattr__(value, "_result_object_identity", id(source_result))
            return value

        def __init__(self, source_result, source_envelope):
            pass

    forged = ForgedContext(result, altered)
    object.__setattr__(
        forged, "_run_provenance", context_provenance(genuine)
    )
    assert _validated_context_envelope(forged) is None
    with pytest.raises(
        DryRunContractError, match="COVERAGE_PROVEN_CONTEXT_BINDING_INVALID"
    ):
        create_first_live_baseline_evidence(forged)
    comparison = compare_coverage_proven_fingerprints(genuine, forged)
    assert comparison.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert comparison.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"


def test_semantic_context_validation_rejects_swap_and_every_altered_fact():
    first = context()
    second = context(change="semantic")
    first_result = _context_result(first)
    second_envelope = _context_envelope(second)

    with pytest.raises(DryRunContractError, match="BINDING_MISMATCH"):
        _create_coverage_proven_batch_context(
            first_result, second_envelope, context_provenance(first)
        )

    rebound_other_snapshot = replace(
        second_envelope, _result_object_identity=id(first_result)
    )
    rebound_context = forged_exact_context(
        first_result, rebound_other_snapshot, context_provenance(first)
    )
    assert _validated_context_envelope(rebound_context) is None

    for field_name, value in (
        ("target_identity_hash", "sha256:" + "1" * 64),
        ("configuration_identity", "sha256:" + "2" * 64),
        ("coverage_identity", "sha256:" + "3" * 64),
        ("mapper_version", "altered-mapper-v2"),
        ("snapshot_schema_version", "altered-snapshot-v2"),
        ("fingerprint_semantics_version", "altered-fingerprint-v2"),
        ("source_fingerprint", "sha256:" + "4" * 64),
    ):
        altered = replace(_context_envelope(first), **{field_name: value})
        altered_context = forged_exact_context(
            first_result, altered, context_provenance(first)
        )
        assert _validated_context_envelope(altered_context) is None
        result = compare_coverage_proven_fingerprints(first, altered_context)
        assert result.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"


def test_fingerprint_comparator_rejects_run_provenance_mismatch():
    genuine = synthetic_context()
    upgraded_envelope = replace(
        _context_envelope(genuine),
        run_mode=RunMode.FIRST_LIVE,
        disposition=SourceHealthDisposition.HUMAN_REVIEW_REQUIRED,
    )
    forged = forged_exact_context(
        _context_result(genuine),
        upgraded_envelope,
        context_provenance(genuine),
    )

    invalid_first = compare_coverage_proven_fingerprints(forged, context())
    invalid_second = compare_coverage_proven_fingerprints(context(), forged)

    assert invalid_first.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert invalid_first.mismatch_code == "F1_COMPARE_FIRST_COVERAGE_INVALID"
    assert invalid_second.outcome is FingerprintComparisonOutcome.NOT_COMPARABLE
    assert invalid_second.mismatch_code == "F1_COMPARE_SECOND_COVERAGE_INVALID"
