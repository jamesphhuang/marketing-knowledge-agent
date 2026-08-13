from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
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
    RunMode,
    SourceHealthDisposition,
    compute_evidence_hash,
    create_first_live_baseline_evidence,
    _context_envelope,
    _create_coverage_proven_batch_context,
)
from marketing_knowledge_agent.google_sheets_read_contracts import (
    ConfiguredRange,
    ConfiguredReadPlan,
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
    SourceHealthError,
    build_coverage_proven_batch_context,
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


def context(*, change: str = "", mode: RunMode = RunMode.SYNTHETIC):
    return build_coverage_proven_batch_context(
        configured_result(change=change),
        run_mode=mode,
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
    built = build_coverage_proven_batch_context(
        result,
        run_mode=RunMode.SYNTHETIC,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    assert called is True
    assert isinstance(built, CoverageProvenBatchContext)

    called = False
    with pytest.raises(
        SourceHealthError, match="SOURCE_HEALTH_CONFIGURED_READ_RESULT_REQUIRED"
    ):
        build_coverage_proven_batch_context(
            result.snapshot,
            run_mode=RunMode.SYNTHETIC,
        )
    assert called is False

    with pytest.raises(TypeError):
        build_coverage_proven_batch_context(
            result.snapshot,
            result.coverage_proof,
            run_mode=RunMode.SYNTHETIC,
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
        build_coverage_proven_batch_context(
            result,
            run_mode=RunMode.SYNTHETIC,
        )


def test_existing_f1_and_version_are_reused_without_redefinition():
    first_result = configured_result()
    second_result = configured_result()
    first = context()
    second = build_coverage_proven_batch_context(
        second_result,
        run_mode=RunMode.SYNTHETIC,
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
    first = build_coverage_proven_batch_context(
        first_result,
        run_mode=RunMode.SYNTHETIC,
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    second = build_coverage_proven_batch_context(
        second_result,
        run_mode=RunMode.SYNTHETIC,
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
    generated = build_coverage_proven_batch_context(
        configured_result(), run_mode=RunMode.SYNTHETIC
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
        build_coverage_proven_batch_context(
            configured_result(),
            run_mode=RunMode.SYNTHETIC,
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
        build_coverage_proven_batch_context(
            configured_result(),
            run_mode=RunMode.SYNTHETIC,
            _correlation_id_factory=lambda: invalid,
        )


def test_dispositions_are_closed_and_caller_cannot_supply_pass_or_threshold():
    synthetic = _context_envelope(context())
    first_live = _context_envelope(context(mode=RunMode.FIRST_LIVE))
    blocked = _context_envelope(context(change="header"))

    assert synthetic.disposition is SourceHealthDisposition.SYNTHETIC_CHECKS_COMPLETE
    assert first_live.disposition is SourceHealthDisposition.HUMAN_REVIEW_REQUIRED
    assert blocked.disposition is SourceHealthDisposition.STRUCTURAL_BLOCK
    assert "PASS" not in {value.value for value in SourceHealthDisposition}
    assert "HEALTHY" not in {value.value for value in SourceHealthDisposition}
    assert "APPROVED" not in {value.value for value in SourceHealthDisposition}

    for forbidden in ("disposition", "threshold", "approved", "baseline_pass"):
        with pytest.raises(TypeError):
            build_coverage_proven_batch_context(
                configured_result(),
                run_mode=RunMode.SYNTHETIC,
                **{forbidden: True},
            )


def test_first_live_evidence_has_exact_allowlist_and_deterministic_hash():
    first = create_first_live_baseline_evidence(context())
    second_context = build_coverage_proven_batch_context(
        configured_result(),
        run_mode=RunMode.SYNTHETIC,
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

    parsed = FirstLiveBaselineEvidence.from_mapping(primitive)
    assert parsed.canonical_json() == first.canonical_json()
    unknown = deepcopy(primitive)
    unknown["unknown"] = "forbidden"
    with pytest.raises(
        DryRunContractError, match="FIRST_LIVE_EVIDENCE_FIELDS_INVALID"
    ):
        FirstLiveBaselineEvidence.from_mapping(unknown)


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
    with pytest.raises(DryRunContractError, match="FIRST_LIVE_EVIDENCE_HASH_MISMATCH"):
        FirstLiveBaselineEvidence.from_mapping(primitive)


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
