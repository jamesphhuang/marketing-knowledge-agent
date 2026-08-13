from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import inspect
import json
import pickle
import uuid

import pytest

from marketing_knowledge_agent.canonical_serialization import (
    compute_source_fingerprint,
)
from marketing_knowledge_agent.google_sheets_dry_run_contracts import (
    CoverageProvenBatchContext,
    DryRunContractError,
    FirstLiveBaselineEvidence,
    SourceHealthDisposition,
    SafeStructuralCounts,
    _canonical_deferred_check_codes,
    _canonical_structural_reason_codes,
    compute_evidence_hash,
    create_first_live_baseline_evidence,
    _context_envelope,
    _create_coverage_proven_batch_context,
)
from marketing_knowledge_agent.google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredReadPlan,
    ConfiguredReadResult,
    ConfiguredSheet,
    REQUIRED_GOOGLE_RESPONSE_FIELDS,
)
from marketing_knowledge_agent.google_sheets_response_mapper import (
    map_google_sheets_response,
)
from marketing_knowledge_agent.google_sheets_runtime_config import (
    production_google_sheets_runtime_config,
)
from marketing_knowledge_agent.google_sheets_source_health import (
    FINGERPRINT_SEMANTICS_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION,
    SensitiveOccupiedRowCounts,
    SourceHealthError,
    build_first_live_coverage_proven_batch_context,
    build_synthetic_coverage_proven_batch_context,
)


FIXED_UUID4 = "12345678-1234-4234-9234-123456789abc"

HEADERS = {
    "merchant_case": (
        "採訪年份",
        "狀態",
        "商家 / 夥伴名稱",
        "Handle",
        "Sales Category LV1",
        "Sales Category LV2",
        "內容相關標籤",
        "文章",
        "影片",
        "Podcast",
        "新聞",
        "備註",
    ),
    "restricted_customer": (
        "更新年份",
        "客戶品牌",
        "網站",
        "Sales Category LV1",
        "是否有簽保密 NDA",
        "NDA是否已上傳Salesforce",
        "店家狀況（例如：店家對中資事件敏感...）",
        "填表人（部門/名字）",
    ),
    "public_metric": (
        "類型",
        "指標",
        "論述",
        "備註",
        "更新時間",
        "參考新聞連結",
        "新聞稿",
        "自媒體",
        "Saleskits",
        "口頭說明",
        "演講簡報",
        "官網/ 招募網站",
        "廣告",
    ),
    "handle_mapping": (
        "Handle",
        "Name (with Link)",
        "Lv1 Sales Category",
        "Lv2 Sales Category 1st",
    ),
}

DATA_SENTINELS = {
    "merchant_case": "MERCHANT_NAME_SENTINEL_WP2_91A7",
    "restricted_customer": "RESTRICTED_CUSTOMER_SENTINEL_WP2_91A7",
    "public_metric": "URL_SENTINEL_WP2_91A7_https://private.invalid",
    "pending_metric": "PENDING_CLAIM_SENTINEL_WP2_91A7",
    "handle_mapping": "HANDLE_NAME_SENTINEL_WP2_91A7",
}


def production_response(*, change: str = "") -> dict:
    plan = production_google_sheets_runtime_config().read_plan
    ranges_by_sheet = {value.sheet_id: value for value in plan.ranges}
    sheets = []
    for configured_sheet in plan.sheets:
        configured_range = ranges_by_sheet[configured_sheet.sheet_id]
        rows = []
        if configured_range.range_id in HEADERS:
            headers = list(HEADERS[configured_range.range_id])
            if change == "header" and configured_range.range_id == "merchant_case":
                headers[0] = "DRIFTED HEADER"
            rows.append(
                {"values": [{"formattedValue": value} for value in headers]}
            )
        data_value = DATA_SENTINELS[configured_range.range_id]
        if change == "semantic" and configured_range.range_id == "public_metric":
            data_value = "CHANGED_PUBLIC_METRIC_SENTINEL_WP2_91A7"
        rows.append({"values": [{"formattedValue": data_value}]})
        properties = {
            "sheetId": configured_sheet.sheet_id,
            "title": configured_sheet.title,
            "hidden": configured_sheet.hidden,
            "gridProperties": {
                "rowCount": configured_sheet.row_count,
                "columnCount": configured_sheet.column_count,
            },
        }
        sheets.append(
            {
                "properties": properties,
                "data": [
                    {
                        "startRow": configured_range.start_row_index,
                        "startColumn": configured_range.start_column_index,
                        "rowData": rows,
                    }
                ],
                "merges": [],
            }
        )
    return {"spreadsheetId": plan.spreadsheet_id, "sheets": sheets}


def configured_result(*, change: str = ""):
    plan = production_google_sheets_runtime_config().read_plan
    return map_google_sheets_response(production_response(change=change), plan)


def context(*, change: str = ""):
    return build_first_live_coverage_proven_batch_context(
        configured_result(change=change),
        _correlation_id_factory=lambda: FIXED_UUID4,
    )


def synthetic_context(*, change: str = ""):
    return build_synthetic_coverage_proven_batch_context(
        configured_result(change=change),
        _correlation_id_factory=lambda: FIXED_UUID4,
    )


def test_only_frozen_configured_read_result_is_accepted(monkeypatch):
    result = configured_result()
    called = False

    def fingerprint(snapshot):
        nonlocal called
        called = True
        return compute_source_fingerprint(snapshot)

    monkeypatch.setattr(
        "marketing_knowledge_agent.google_sheets_source_health.compute_source_fingerprint",
        fingerprint,
    )
    built = build_synthetic_coverage_proven_batch_context(
        result,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    assert called is True
    assert isinstance(built, CoverageProvenBatchContext)

    called = False
    with pytest.raises(
        SourceHealthError, match="SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED"
    ):
        build_synthetic_coverage_proven_batch_context(
            result.snapshot,
        )
    assert called is False

    with pytest.raises(TypeError):
        build_synthetic_coverage_proven_batch_context(
            result.snapshot,
            result.coverage_proof,
        )


def test_mismatched_configuration_is_rejected_before_f1(monkeypatch):
    plan = ConfiguredReadPlan(
        spreadsheet_id="synthetic-other-target",
        config_version="synthetic-other-config-v1",
        sheets=(ConfiguredSheet(7, "Other", False, 2, 2),),
        ranges=(ConfiguredRange("other", 7, 0, 2, 0, 2),),
        fields=REQUIRED_GOOGLE_RESPONSE_FIELDS,
    )
    result = map_google_sheets_response(
        {
            "spreadsheetId": "synthetic-other-target",
            "sheets": [
                {
                    "properties": {
                        "sheetId": 7,
                        "title": "Other",
                        "hidden": False,
                        "gridProperties": {"rowCount": 2, "columnCount": 2},
                    },
                    "data": [{"startRow": 0, "startColumn": 0, "rowData": []}],
                    "merges": [],
                }
            ],
        },
        plan,
    )
    monkeypatch.setattr(
        "marketing_knowledge_agent.google_sheets_source_health.compute_source_fingerprint",
        lambda snapshot: pytest.fail("F1 ran before frozen selection validation"),
    )
    with pytest.raises(
        SourceHealthError, match="SOURCE_HEALTH_FROZEN_SELECTION_MISMATCH"
    ):
        build_synthetic_coverage_proven_batch_context(
            result,
        )


def test_existing_f1_and_version_are_reused_without_redefinition():
    first_result = configured_result()
    second_result = configured_result()
    first = context()
    second = build_first_live_coverage_proven_batch_context(
        second_result,
        _correlation_id_factory=lambda: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    first_envelope = _context_envelope(first)
    second_envelope = _context_envelope(second)

    assert first_envelope.source_fingerprint == compute_source_fingerprint(
        first_result.snapshot
    )
    assert first_envelope.source_fingerprint == second_envelope.source_fingerprint
    assert first_envelope.fingerprint_semantics_version == (
        "canonical-source-snapshot-v1"
    )
    assert first_envelope.snapshot_schema_version == SNAPSHOT_SCHEMA_VERSION
    assert _context_envelope(context(change="semantic")).source_fingerprint != (
        first_envelope.source_fingerprint
    )


def test_context_is_exactly_bound_opaque_immutable_and_nonserializable():
    first_result = configured_result()
    second_result = configured_result(change="semantic")
    first = build_first_live_coverage_proven_batch_context(
        first_result,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    second = build_first_live_coverage_proven_batch_context(
        second_result,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )

    with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
        CoverageProvenBatchContext()
    with pytest.raises(DryRunContractError, match="BINDING_MISMATCH"):
        _create_coverage_proven_batch_context(first_result, _context_envelope(second))
    with pytest.raises(AttributeError, match="IMMUTABLE"):
        first.envelope = _context_envelope(first)
    with pytest.raises(TypeError, match="PICKLE_FORBIDDEN"):
        pickle.dumps(first)
    with pytest.raises(TypeError):
        json.dumps(first)
    with pytest.raises(TypeError):
        asdict(first)

    assert repr(first) == "CoverageProvenBatchContext(<sensitive>)"
    assert not hasattr(first, "__dict__")
    assert not hasattr(first, "to_dict")
    assert not hasattr(first, "to_json")
    assert not hasattr(first, "model_dump")


def test_correlation_id_is_service_generated_and_strict_uuid4():
    generated = build_synthetic_coverage_proven_batch_context(
        configured_result()
    )
    generated_value = _context_envelope(generated).correlation_id
    parsed = uuid.UUID(generated_value)
    assert len(generated_value) == 36
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122
    assert str(parsed) == generated_value

    injected = context()
    assert _context_envelope(injected).correlation_id == FIXED_UUID4
    with pytest.raises(TypeError):
        build_synthetic_coverage_proven_batch_context(
            configured_result(),
            correlation_id=FIXED_UUID4,
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "12345678-1234-4234-9234-123456789ABC",
        "12345678-1234-1234-9234-123456789abc",
        " 12345678-1234-4234-9234-123456789abc",
        "12345678-1234-4234-9234-123456789abc\n",
        "12345678-1234-4234-9234-123456789abc-extra",
    ],
)
def test_private_correlation_id_seam_rejects_invalid_values(invalid):
    with pytest.raises(
        SourceHealthError, match="SOURCE_HEALTH_CORRELATION_ID_INVALID"
    ):
        build_synthetic_coverage_proven_batch_context(
            configured_result(),
            _correlation_id_factory=lambda: invalid,
        )


def test_dispositions_are_closed_and_caller_cannot_supply_pass_or_threshold():
    synthetic = _context_envelope(synthetic_context())
    first_live = _context_envelope(context())
    blocked = _context_envelope(context(change="header"))

    assert synthetic.disposition is SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE
    assert first_live.disposition is SourceHealthDisposition.HUMAN_REVIEW_REQUIRED
    assert blocked.disposition is SourceHealthDisposition.STRUCTURAL_BLOCK
    assert "PASS" not in {value.value for value in SourceHealthDisposition}
    assert "HEALTHY" not in {value.value for value in SourceHealthDisposition}
    assert "APPROVED" not in {value.value for value in SourceHealthDisposition}

    for forbidden in ("disposition", "threshold", "approved", "baseline_pass"):
        with pytest.raises(TypeError):
            build_synthetic_coverage_proven_batch_context(
                configured_result(),
                **{forbidden: True},
            )


def test_first_live_evidence_has_exact_allowlist_and_deterministic_hash():
    first = create_first_live_baseline_evidence(context())
    second_context = build_first_live_coverage_proven_batch_context(
        configured_result(),
        _correlation_id_factory=lambda: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    second = create_first_live_baseline_evidence(second_context)
    expected_fields = {
        "schema_version",
        "evidence_kind",
        "authority",
        "review_scope",
        "target_identity_hash",
        "configuration_identity",
        "config_version",
        "coverage_identity",
        "mapper_version",
        "snapshot_schema_version",
        "fingerprint_semantics_version",
        "source_health_rules_version",
        "source_fingerprint",
        "safe_counts",
        "structural_reason_codes",
        "deferred_check_codes",
        "disposition",
        "evidence_hash",
    }
    primitive = first.canonical_mapping()

    assert set(primitive) == expected_fields
    assert first.canonical_json() == second.canonical_json()
    assert first.evidence_hash == second.evidence_hash
    assert first.evidence_hash == compute_evidence_hash(first)
    assert first.evidence_hash.startswith("sha256:")
    assert len(first.evidence_hash) == 71
    assert first.fingerprint_semantics_version == FINGERPRINT_SEMANTICS_VERSION
    assert "correlation_id" not in primitive
    assert "timestamp" not in primitive
    assert "latency" not in primitive
    assert "retry_count" not in primitive
    assert "threshold" not in primitive
    assert "human_approval" not in primitive

    assert not hasattr(FirstLiveBaselineEvidence, "from_mapping")
    unknown = deepcopy(primitive)
    unknown["unknown"] = "forbidden"
    assert not callable(getattr(FirstLiveBaselineEvidence, "from_mapping", None))


def test_evidence_hash_is_domain_separated_and_rejects_tampering():
    evidence = create_first_live_baseline_evidence(context())
    primitive = evidence.canonical_mapping()
    bare_json_hash = "sha256:" + __import__("hashlib").sha256(
        json.dumps(
            {key: value for key, value in primitive.items() if key != "evidence_hash"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert evidence.evidence_hash != bare_json_hash

    primitive["evidence_hash"] = "sha256:" + "0" * 64
    assert not hasattr(FirstLiveBaselineEvidence, "from_mapping")


def test_evidence_excludes_sensitive_counts_payload_and_forbidden_surfaces():
    evidence = create_first_live_baseline_evidence(context())
    envelope = _context_envelope(context())
    rendered = evidence.canonical_json()
    safe_count_keys = set(evidence.safe_counts.as_dict())

    assert envelope.sensitive_occupied_row_counts.restricted_customer == 1
    assert envelope.sensitive_occupied_row_counts.pending_metric == 1
    assert "restricted_customer" not in safe_count_keys
    assert "pending_metric" not in safe_count_keys
    assert "merchant_case" not in safe_count_keys
    assert "public_metric" not in safe_count_keys
    assert "handle_mapping" not in safe_count_keys
    assert evidence.safe_counts.structural_issue_count == 0
    assert evidence.deferred_check_codes
    assert all("DEFERRED" in code for code in evidence.deferred_check_codes)
    assert not any("missing" in key.lower() for key in safe_count_keys)

    forbidden = tuple(DATA_SENTINELS.values()) + (
        production_google_sheets_runtime_config().read_plan.spreadsheet_id,
        "Authorization: Bearer",
        "raw_cells",
        "canonical_entity_count",
        "governance_exclusion_count",
        "MREC_missing_count",
        "WP4",
        "release",
        "activation",
    )
    for sentinel in forbidden:
        assert sentinel not in rendered
        assert sentinel not in repr(evidence)
        assert sentinel not in repr(envelope)


def test_evidence_constructor_and_context_projection_fail_closed():
    with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
        FirstLiveBaselineEvidence()
    with pytest.raises(
        DryRunContractError, match="COVERAGE_PROVEN_CONTEXT_BINDING_INVALID"
    ):
        create_first_live_baseline_evidence(object())


def test_envelope_versions_safe_counts_and_reason_codes_are_reconciled():
    envelope = _context_envelope(context())
    blocked = _context_envelope(context(change="header"))

    assert envelope.schema_version == SOURCE_HEALTH_ENVELOPE_SCHEMA_VERSION
    assert envelope.safe_counts.configured_range_count == 5
    assert envelope.safe_counts.covered_range_count == 5
    assert envelope.safe_counts.configured_sheet_count == 5
    assert envelope.safe_counts.observed_sheet_count == 5
    assert envelope.safe_counts.critical_sheet_expected_count == 5
    assert envelope.safe_counts.critical_sheet_observed_count == 5
    assert envelope.safe_counts.header_binding_expected_count == 4
    assert envelope.safe_counts.header_binding_valid_count == 4
    assert envelope.safe_counts.positional_binding_expected_count == 1
    assert envelope.safe_counts.positional_binding_valid_count == 1
    assert envelope.structural_reason_codes == ()
    assert blocked.structural_reason_codes == (
        "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
    )
    assert blocked.safe_counts.structural_issue_count == len(
        blocked.structural_reason_codes
    )
    assert blocked.safe_counts.header_binding_valid_count == 3


def test_configured_result_boundary_rejects_subclasses_and_duck_types():
    genuine = configured_result()
    other = configured_result(change="semantic")

    class ForgedConfiguredReadResult(ConfiguredReadResult):
        __slots__ = ()

        def __new__(cls, snapshot, proof, identity):
            value = object.__new__(cls)
            object.__setattr__(value, "_snapshot", snapshot)
            object.__setattr__(value, "_coverage_proof", proof)
            object.__setattr__(value, "_configuration_identity", identity)
            return value

        def __init__(self, snapshot, proof, identity):
            pass

    forged_proof = ForgedConfiguredReadResult(
        genuine.snapshot,
        other.coverage_proof,
        genuine.configuration_identity,
    )
    forged_snapshot = ForgedConfiguredReadResult(
        other.snapshot,
        genuine.coverage_proof,
        genuine.configuration_identity,
    )

    for value in (forged_proof, forged_snapshot):
        with pytest.raises(
            SourceHealthError, match="SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED"
        ):
            build_first_live_coverage_proven_batch_context(value)

    class DuckResult:
        snapshot = genuine.snapshot
        coverage_proof = genuine.coverage_proof
        configuration_identity = genuine.configuration_identity

    with pytest.raises(
        SourceHealthError, match="SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED"
    ):
        build_synthetic_coverage_proven_batch_context(DuckResult())


def test_mode_specific_builders_have_no_caller_mode_or_disposition_authority():
    first_signature = inspect.signature(
        build_first_live_coverage_proven_batch_context
    )
    synthetic_signature = inspect.signature(
        build_synthetic_coverage_proven_batch_context
    )
    assert "run_mode" not in first_signature.parameters
    assert "run_mode" not in synthetic_signature.parameters

    for builder in (
        build_first_live_coverage_proven_batch_context,
        build_synthetic_coverage_proven_batch_context,
    ):
        with pytest.raises(TypeError):
            builder(configured_result(), run_mode="FIRST_LIVE")
        with pytest.raises(TypeError):
            builder(
                configured_result(),
                disposition=SourceHealthDisposition.HUMAN_REVIEW_REQUIRED,
            )

    assert _context_envelope(context()).disposition is (
        SourceHealthDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert _context_envelope(synthetic_context()).disposition is (
        SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE
    )


def test_synthetic_context_cannot_form_authoritative_first_live_evidence():
    with pytest.raises(
        DryRunContractError,
        match="FIRST_LIVE_EVIDENCE_FIRST_LIVE_CONTEXT_REQUIRED",
    ):
        create_first_live_baseline_evidence(synthetic_context())

    assert not hasattr(FirstLiveBaselineEvidence, "from_mapping")
    with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
        FirstLiveBaselineEvidence(**create_first_live_baseline_evidence(context()).canonical_mapping())


def test_code_allowlists_reject_cross_category_payload_duplicates_and_unknowns():
    envelope = _context_envelope(context(change="header"))
    assert _canonical_structural_reason_codes(
        list(reversed(envelope.structural_reason_codes))
    ) == envelope.structural_reason_codes
    assert _canonical_deferred_check_codes(
        list(reversed(envelope.deferred_check_codes))
    ) == envelope.deferred_check_codes

    structural = envelope.structural_reason_codes[0]
    deferred = envelope.deferred_check_codes[0]
    invalid_structural = (
        deferred,
        "UNKNOWN_REASON",
        "CUSTOMER_SECRET_SENTINEL",
        "UNKNOWN\nREASON",
        "UNKNOWN\x00REASON",
        "X" * 10_000,
    )
    for code in invalid_structural:
        with pytest.raises(DryRunContractError):
            _canonical_structural_reason_codes((code,))
    with pytest.raises(DryRunContractError):
        _canonical_deferred_check_codes((structural,))
    with pytest.raises(DryRunContractError):
        _canonical_structural_reason_codes((structural, structural))
    with pytest.raises(DryRunContractError):
        _canonical_deferred_check_codes((deferred, deferred))


def test_code_allowlists_cover_every_wp2_emitted_stable_code():
    structural = (
        "SOURCE_HEALTH_CRITICAL_SHEET_PROFILE_MISMATCH",
        "SOURCE_HEALTH_CRITICAL_SHEET_SET_MISMATCH",
        "SOURCE_HEALTH_HANDLE_MAPPING_HEADER_MISMATCH",
        "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
        "SOURCE_HEALTH_PENDING_POSITIONAL_BINDING_MISMATCH",
        "SOURCE_HEALTH_PUBLIC_METRIC_HEADER_MISMATCH",
        "SOURCE_HEALTH_RESTRICTED_CUSTOMER_HEADER_MISMATCH",
    )
    deferred = (
        "BRAND_ID_INITIAL_REVIEW_DEFERRED",
        "BRAND_ID_MAPPING_AUTHORITY_DEFERRED",
        "MERCHANT_BRD_ASSIGNMENT_DEFERRED",
        "MERCHANT_ID_REVIEW_STATUS_DEFERRED",
        "MERCHANT_MREC_ASSIGNMENT_DEFERRED",
        "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED",
    )

    assert _canonical_structural_reason_codes(
        list(reversed(structural))
    ) == structural
    assert _canonical_deferred_check_codes(list(reversed(deferred))) == deferred


@pytest.mark.parametrize(
    "field_name",
    [
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
    ],
)
@pytest.mark.parametrize("invalid", [True, False, -1, 1.0, "1", 10**100])
def test_safe_counts_reject_invalid_or_impossible_values(field_name, invalid):
    values = _context_envelope(context()).safe_counts.as_dict()
    values[field_name] = invalid
    with pytest.raises(DryRunContractError):
        SafeStructuralCounts(**values)


def test_safe_count_relations_and_sensitive_capacity_fail_closed():
    values = _context_envelope(context()).safe_counts.as_dict()
    for field_name, invalid in (
        ("covered_range_count", 6),
        ("observed_sheet_count", 6),
        ("critical_sheet_observed_count", 6),
        ("critical_sheet_observed_count", 5),
        ("header_binding_valid_count", 5),
        ("positional_binding_valid_count", 2),
    ):
        changed = dict(values)
        if field_name == "critical_sheet_observed_count" and invalid == 5:
            changed["observed_sheet_count"] = 4
        changed[field_name] = invalid
        with pytest.raises(DryRunContractError):
            SafeStructuralCounts(**changed)

    capacities = {
        "merchant_case": 1012,
        "restricted_customer": 990,
        "public_metric": 993,
        "pending_metric": 997,
        "handle_mapping": 997,
    }
    for field_name, maximum in capacities.items():
        invalid = dict(capacities)
        invalid[field_name] = maximum + 1
        with pytest.raises(SourceHealthError):
            SensitiveOccupiedRowCounts(**invalid)
    boundary = SensitiveOccupiedRowCounts(
        **capacities,
    )
    assert boundary.merchant_case == 1012


def test_envelope_defensively_canonicalizes_nested_collections_and_mappings():
    result = configured_result(change="header")
    original_context = build_first_live_coverage_proven_batch_context(
        result, _correlation_id_factory=lambda: FIXED_UUID4
    )
    original = _context_envelope(original_context)
    reason_input = list(original.structural_reason_codes)
    deferred_input = list(reversed(original.deferred_check_codes))
    safe_mapping = original.safe_counts.as_dict()
    sensitive_mapping = {
        "merchant_case": 1,
        "restricted_customer": 1,
        "public_metric": 1,
        "pending_metric": 1,
        "handle_mapping": 1,
    }
    copied = replace(
        original,
        structural_reason_codes=reason_input,
        deferred_check_codes=deferred_input,
        safe_counts=safe_mapping,
        sensitive_occupied_row_counts=sensitive_mapping,
    )
    baseline_hash = create_first_live_baseline_evidence(
        _create_coverage_proven_batch_context(result, copied)
    ).evidence_hash
    reason_input.clear()
    deferred_input.clear()
    safe_mapping.clear()
    sensitive_mapping.clear()

    assert type(copied.structural_reason_codes) is tuple
    assert type(copied.deferred_check_codes) is tuple
    assert type(copied.safe_counts) is SafeStructuralCounts
    assert type(copied.sensitive_occupied_row_counts) is SensitiveOccupiedRowCounts
    assert copied.structural_reason_codes == original.structural_reason_codes
    assert copied.deferred_check_codes == original.deferred_check_codes
    assert create_first_live_baseline_evidence(
        _create_coverage_proven_batch_context(result, copied)
    ).evidence_hash == baseline_hash

    with pytest.raises(AttributeError):
        copied.safe_counts.configured_range_count = 4
    with pytest.raises(AttributeError):
        copied.sensitive_occupied_row_counts.merchant_case = 0

    invalid_safe_mapping = original.safe_counts.as_dict()
    invalid_safe_mapping["unknown"] = 0
    with pytest.raises(SourceHealthError):
        replace(original, safe_counts=invalid_safe_mapping)

    invalid_sensitive_mapping = {
        "merchant_case": 1,
        "restricted_customer": 1,
        "public_metric": 1,
        "pending_metric": 1,
        "handle_mapping": 1,
        "unknown": 0,
    }
    with pytest.raises(SourceHealthError):
        replace(original, sensitive_occupied_row_counts=invalid_sensitive_mapping)


def test_every_evidence_semantic_field_changes_the_integrity_hash():
    mutations = {
        "schema_version": "mutated-schema",
        "evidence_kind": "mutated-kind",
        "authority": "mutated-authority",
        "review_scope": "mutated-scope",
        "target_identity_hash": "sha256:" + "1" * 64,
        "configuration_identity": "sha256:" + "2" * 64,
        "config_version": "mutated-config",
        "coverage_identity": "sha256:" + "3" * 64,
        "mapper_version": "mutated-mapper",
        "snapshot_schema_version": "mutated-snapshot-schema",
        "fingerprint_semantics_version": "mutated-fingerprint-semantics",
        "source_health_rules_version": "mutated-source-health-rules",
        "source_fingerprint": "sha256:" + "4" * 64,
        "safe_counts": replace(
            _context_envelope(context()).safe_counts,
            covered_range_count=4,
        ),
        "structural_reason_codes": (
            "SOURCE_HEALTH_MERCHANT_CASE_HEADER_MISMATCH",
        ),
        "deferred_check_codes": (),
        "disposition": SourceHealthDisposition.STRUCTURAL_BLOCK,
    }

    assert len(mutations) == 17
    for field_name, changed_value in mutations.items():
        evidence = create_first_live_baseline_evidence(context())
        original_hash = evidence.evidence_hash
        object.__setattr__(evidence, field_name, changed_value)
        assert compute_evidence_hash(evidence) != original_hash, field_name


def test_payload_bearing_test_seam_exception_has_no_cause_or_context(caplog):
    secret = "NESTED_EXCEPTION_SECRET_SENTINEL"

    def hostile_factory():
        raise ValueError(secret)

    with pytest.raises(SourceHealthError) as caught:
        build_synthetic_coverage_proven_batch_context(
            configured_result(), _correlation_id_factory=hostile_factory
        )

    rendered = str(caught.value) + repr(caught.value)
    assert secret not in rendered
    assert secret not in caplog.text
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
