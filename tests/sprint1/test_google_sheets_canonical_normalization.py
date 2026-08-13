from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import pickle
import socket
from typing import Optional, Union

import pytest

import marketing_knowledge_agent.google_sheets_canonical_normalization as wp3

from marketing_knowledge_agent.brand_review_candidates import BrandReviewCandidate
from marketing_knowledge_agent.canonical_models import (
    Brand,
    BrandIdentityDecision,
    PublicMetric,
    ReviewStatus,
    SourceRecord,
)
from marketing_knowledge_agent.google_normalization import ExcludedSourceRef
from marketing_knowledge_agent.google_sheets_canonical_normalization import (
    CanonicalNormalizationBatch,
    CanonicalNormalizationError,
    HandleMappingStaging,
    IdNamespace,
    MerchantCaseStaging,
    PendingMetricStaging,
    PublicMetricStaging,
    RestrictedDenylistStaging,
    WP3_FIELD_REGISTRY,
    normalize_coverage_proven_batch,
)
from marketing_knowledge_agent.google_sheets_dry_run_contracts import (
    CoverageProvenBatchContext,
    _context_envelope,
    _context_result,
)
from marketing_knowledge_agent.google_sheets_read_contracts import ConfiguredReadResult
from marketing_knowledge_agent.google_sheets_response_mapper import (
    map_google_sheets_response,
)
from marketing_knowledge_agent.google_sheets_runtime_config import (
    production_google_sheets_runtime_config,
)
from marketing_knowledge_agent.google_sheets_source_health import (
    SourceHealthEnvelope,
    build_first_live_coverage_proven_batch_context,
    build_synthetic_coverage_proven_batch_context,
)
from marketing_knowledge_agent.sheets_contracts import SpreadsheetSnapshot
from sprint1.test_google_sheets_dry_run_contracts import (
    FIXED_UUID4,
    HEADERS,
    context_provenance,
    forged_exact_context,
)


def text(value: str, **extra) -> dict:
    return {"formattedValue": value, "effectiveValue": {"stringValue": value}, **extra}


def number(value: Union[int, float], *, formatted: Optional[str] = None) -> dict:
    cell = {"effectiveValue": {"numberValue": value}}
    if formatted is not None:
        cell["formattedValue"] = formatted
    return cell


def checkbox(value: bool, *, validation: bool = True) -> dict:
    cell = {
        "formattedValue": "TRUE" if value else "FALSE",
        "effectiveValue": {"boolValue": value},
    }
    if validation:
        cell["dataValidation"] = {"condition": {"type": "BOOLEAN"}}
    return cell


def formula(display: str, expression: str, *, cached: bool = True) -> dict:
    cell = {
        "formattedValue": display,
        "userEnteredValue": {"formulaValue": expression},
    }
    if cached:
        cell["effectiveValue"] = {"stringValue": display}
    return cell


def full_rows() -> dict[str, list[list[dict]]]:
    return {
        "merchant_case": [[
            text("2024"), text("訪談完成"),
            text("Merchant Secret", hyperlink="https://shop.example/about?utm_source=x"),
            text(" @Example "), text("Retail"), text("Food"),
            text(" Growth，NEWS、growth "), text("Article"), {}, {}, {}, text("merchant-note"),
        ]],
        "restricted_customer": [[
            text("2025"), text("RESTRICTED_BRAND_SENTINEL"),
            text("RESTRICTED_WEBSITE_SENTINEL", hyperlink="https://restricted.example"),
            text("Restricted"), checkbox(False, validation=False), {},
            text("RESTRICTED_NOTE_SENTINEL"), text("SUBMITTED_BY_SENTINEL"),
        ]],
        "public_metric": [[
            text("GMV"), text("Growth"), text("Public claim"), {},
            text("2026-08-14"), text("Evidence", hyperlink="https://evidence.example/report"),
            checkbox(True), checkbox(False), checkbox(False), checkbox(False),
            checkbox(False), checkbox(False), checkbox(False),
        ]],
        "pending_metric": [[
            text("Draft"), text("Pending"), text("PENDING_BODY_SENTINEL"),
            text("PENDING_NOTE_SENTINEL"),
        ]],
        "handle_mapping": [[
            text("@example"), text("Mapping Secret", hyperlink="https://shop.example/about"),
            text("Suggested"), text("Suggested 2"),
        ]],
    }


def production_response(
    rows_by_source: Optional[dict[str, list[list[dict]]]] = None,
    *,
    merges_by_source: Optional[dict[str, list[dict]]] = None,
    reverse_sheets: bool = False,
) -> dict:
    plan = production_google_sheets_runtime_config().read_plan
    ranges = {item.sheet_id: item for item in plan.ranges}
    supplied = rows_by_source or full_rows()
    merges_by_source = merges_by_source or {}
    sheets = []
    for configured_sheet in plan.sheets:
        configured_range = ranges[configured_sheet.sheet_id]
        row_data = []
        if configured_range.range_id in HEADERS:
            row_data.append(
                {"values": [{"formattedValue": value} for value in HEADERS[configured_range.range_id]]}
            )
        row_data.extend({"values": row} for row in supplied.get(configured_range.range_id, []))
        sheets.append(
            {
                "properties": {
                    "sheetId": configured_sheet.sheet_id,
                    "title": configured_sheet.title,
                    "hidden": configured_sheet.hidden,
                    "gridProperties": {
                        "rowCount": configured_sheet.row_count,
                        "columnCount": configured_sheet.column_count,
                    },
                },
                "data": [{
                    "startRow": configured_range.start_row_index,
                    "startColumn": configured_range.start_column_index,
                    "rowData": row_data,
                }],
                "merges": merges_by_source.get(configured_range.range_id, []),
            }
        )
    if reverse_sheets:
        sheets.reverse()
    return {"spreadsheetId": plan.spreadsheet_id, "sheets": sheets}


def synthetic_context(
    rows_by_source: Optional[dict[str, list[list[dict]]]] = None,
    *,
    merges_by_source: Optional[dict[str, list[dict]]] = None,
    reverse_sheets: bool = False,
):
    plan = production_google_sheets_runtime_config().read_plan
    result = map_google_sheets_response(
        production_response(
            rows_by_source,
            merges_by_source=merges_by_source,
            reverse_sheets=reverse_sheets,
        ),
        plan,
    )
    return build_synthetic_coverage_proven_batch_context(
        result, _correlation_id_factory=lambda: FIXED_UUID4
    )


def test_exact_genuine_synthetic_context_is_the_only_supported_input():
    genuine = synthetic_context()
    result = _context_result(genuine)
    envelope = _context_envelope(genuine)

    assert type(normalize_coverage_proven_batch(genuine)) is CanonicalNormalizationBatch
    for unsupported in (result.snapshot, result, envelope, object()):
        with pytest.raises(CanonicalNormalizationError, match="WP3_COVERAGE_PROVEN_CONTEXT_REQUIRED"):
            normalize_coverage_proven_batch(unsupported)

    first_live = build_first_live_coverage_proven_batch_context(
        result, _correlation_id_factory=lambda: FIXED_UUID4
    )
    with pytest.raises(CanonicalNormalizationError, match="TRUSTED_SYNTHETIC"):
        normalize_coverage_proven_batch(first_live)


def test_context_subclass_forgery_transplant_and_alteration_fail_closed():
    genuine = synthetic_context()

    class Subclass(CoverageProvenBatchContext):
        pass

    with pytest.raises(CanonicalNormalizationError, match="CONTEXT_REQUIRED"):
        normalize_coverage_proven_batch(object.__new__(Subclass))

    other = synthetic_context({**full_rows(), "merchant_case": [[{}, {}, text("Other")]]})
    transplanted = forged_exact_context(
        _context_result(genuine), _context_envelope(other), context_provenance(genuine)
    )
    altered_envelope = replace(
        _context_envelope(genuine), source_fingerprint="sha256:" + "9" * 64
    )
    altered = forged_exact_context(
        _context_result(genuine), altered_envelope, context_provenance(genuine)
    )
    for invalid in (transplanted, altered):
        with pytest.raises(CanonicalNormalizationError, match="CONTEXT_INVALID"):
            normalize_coverage_proven_batch(invalid)


def test_structural_block_is_rejected_before_snapshot_use():
    response = production_response()
    response["sheets"][0]["data"][0]["rowData"][0]["values"][0] = {
        "formattedValue": "DRIFT"
    }
    plan = production_google_sheets_runtime_config().read_plan
    blocked = build_synthetic_coverage_proven_batch_context(
        map_google_sheets_response(response, plan),
        _correlation_id_factory=lambda: FIXED_UUID4,
    )
    with pytest.raises(CanonicalNormalizationError, match="TRUSTED_SYNTHETIC"):
        normalize_coverage_proven_batch(blocked)


def test_registry_is_exactly_the_frozen_five_source_interpretation():
    assert [(item.source_class, item.sheet_id, item.title) for item in WP3_FIELD_REGISTRY] == [
        ("merchant_case", 0, "商家/夥伴案例資料庫"),
        ("restricted_customer", 1456785208, "「不可公開」客戶名單"),
        ("public_metric", 918878896, "「可公開」對外數據"),
        ("pending_metric", 956677822, "待確認數據"),
        ("handle_mapping", 737692182, "handle 比對"),
    ]
    assert [
        (item.header_row, item.first_data_row, item.last_data_row, item.first_column, item.last_column)
        for item in WP3_FIELD_REGISTRY
    ] == [
        (6, 7, 1018, "A", "L"),
        (4, 5, 994, "A", "H"),
        (6, 7, 999, "A", "M"),
        (None, 3, 999, "A", "D"),
        (1, 2, 998, "A", "D"),
    ]
    public = next(item for item in WP3_FIELD_REGISTRY if item.source_class == "public_metric")
    assert {field.column for field in public.fields if field.merge_inheritance_allowed} == {"A", "B", "F"}
    assert {
        item.source_class: tuple((field.column, field.name) for field in item.fields)
        for item in WP3_FIELD_REGISTRY
    } == {
        "merchant_case": (
            ("A", "interview_year"), ("B", "source_status"), ("C", "merchant_name"),
            ("D", "normalized_handle"), ("E", "sales_category_lv1"),
            ("F", "sales_category_lv2"), ("G", "content_tags"), ("H", "article"),
            ("I", "video"), ("J", "podcast"), ("K", "news"), ("L", "notes"),
        ),
        "restricted_customer": (
            ("A", "updated_year"), ("B", "customer_brand"), ("C", "website"),
            ("D", "sales_category_lv1"), ("E", "nda_signed"), ("F", "nda_uploaded"),
            ("G", "restricted_reason"), ("H", "submitted_by"),
        ),
        "public_metric": (
            ("A", "metric_type"), ("B", "indicator"), ("C", "statement"),
            ("D", "note"), ("E", "updated_date"), ("F", "evidence_url"),
            ("G", "press_release"), ("H", "owned_media"), ("I", "saleskits"),
            ("J", "verbal_briefing"), ("K", "speaking_deck"),
            ("L", "website_recruiting"), ("M", "ads"),
        ),
        "pending_metric": (
            ("A", "metric_type"), ("B", "indicator"),
            ("C", "statement"), ("D", "note"),
        ),
        "handle_mapping": (
            ("A", "normalized_handle"), ("B", "name_with_link"),
            ("C", "category_lv1"), ("D", "category_lv2"),
        ),
    }


def test_common_text_handle_tag_year_row_and_lineage_normalization():
    batch = normalize_coverage_proven_batch(synthetic_context())
    merchant = batch.merchant_cases[0]

    assert merchant.interview_year == 2024
    assert merchant.normalized_handle == "@example"
    assert merchant.tags == ("growth", "news")
    assert merchant.source_lineage.source_row == 7
    assert merchant.source_lineage.source_columns["merchant_name"] == "C"
    assert merchant.source_lineage.source_ranges["merchant_name"] == "C7"
    assert merchant.identity_state.value == "SCHEMA_DEFERRED"
    assert merchant.brand_state.value == "NEEDS_REVIEW"
    assert merchant.publish_eligibility.value == "needs_review"


@pytest.mark.parametrize(
    ("date_cell", "expected"),
    [
        (number(0), date(1899, 12, 30)),
        (text("2026-08-14"), date(2026, 8, 14)),
        (text("2026/08/14"), date(2026, 8, 14)),
        (text("2026.08.14"), date(2026, 8, 14)),
    ],
)
def test_date_only_accepts_integral_serial_and_exact_text(date_cell, expected):
    rows = full_rows()
    rows["public_metric"][0][4] = date_cell
    metric = normalize_coverage_proven_batch(synthetic_context(rows)).public_metrics[0]
    assert metric.maintenance_updated_at == expected


@pytest.mark.parametrize(
    "date_cell",
    [number(1.5), number(-1), text("2026-08"), text("2026"), text("08/14/2026")],
)
def test_ambiguous_or_non_date_values_need_review_without_date_invention(date_cell):
    rows = full_rows()
    rows["public_metric"][0][4] = date_cell
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert batch.public_metrics == ()
    assert "PUBLIC_METRIC_DATE_NEEDS_REVIEW" in {
        code for fact in batch.review_facts for code in fact.reason_codes
    }


def test_formula_uses_cached_display_and_missing_cache_fails_closed():
    rows = full_rows()
    rows["merchant_case"][0][2] = formula(
        "Formula Merchant", '=HYPERLINK("https://formula.example/path","Formula Merchant")'
    )
    rows["handle_mapping"] = []
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert batch.merchant_cases[0].name == "Formula Merchant"
    assert batch.brand_review_candidates[0].website_hosts == ("formula.example",)

    rows["merchant_case"][0][2] = formula(
        "NO_CACHE_SENTINEL", '=HYPERLINK("https://formula.example","NO_CACHE_SENTINEL")', cached=False
    )
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert batch.merchant_cases[0].name is None
    assert "CELL_FORMULA_EFFECTIVE_VALUE_MISSING" in {
        code for fact in batch.review_facts for code in fact.reason_codes
    }

    metric_rows = full_rows()
    metric_rows["public_metric"][0][4] = formula(
        "2026-08-14", "=TODAY()", cached=False
    )
    batch = normalize_coverage_proven_batch(synthetic_context(metric_rows))
    assert batch.public_metrics == ()
    assert "PUBLIC_METRIC_SOURCE_NORMALIZATION_FAILED" in {
        code for fact in batch.review_facts for code in fact.reason_codes
    }


def test_public_metric_only_a_b_f_merge_inherit_and_lineage_preserves_range():
    rows = full_rows()
    first = rows["public_metric"][0]
    second = [{}, {}, text("Second claim"), {}, {}, {}, *[checkbox(False) for _ in range(7)]]
    rows["public_metric"] = [first, second]
    merges = {
        "public_metric": [
            {"sheetId": 918878896, "startRowIndex": 6, "endRowIndex": 8, "startColumnIndex": 0, "endColumnIndex": 1},
            {"sheetId": 918878896, "startRowIndex": 6, "endRowIndex": 8, "startColumnIndex": 1, "endColumnIndex": 2},
            {"sheetId": 918878896, "startRowIndex": 6, "endRowIndex": 8, "startColumnIndex": 5, "endColumnIndex": 6},
        ]
    }
    batch = normalize_coverage_proven_batch(synthetic_context(rows, merges_by_source=merges))

    assert len(batch.public_metrics) == 2
    second_metric = batch.public_metrics[1]
    assert second_metric.metric_type == "GMV"
    assert second_metric.indicator == "Growth"
    assert second_metric.evidence_urls == ("https://evidence.example/report",)
    assert second_metric.source_lineage.source_row == 8
    assert second_metric.source_lineage.source_ranges["metric_type"] == "A7:A8"


def test_merchant_rows_are_never_fill_down_or_auto_merged_and_exact_duplicates_only_review():
    rows = full_rows()
    merchant = rows["merchant_case"][0]
    rows["merchant_case"] = [merchant, list(merchant)]
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert len(batch.merchant_cases) == 2
    assert len(batch.duplicate_review_facts) == 1
    assert len(batch.duplicate_review_facts[0].source_refs) == 2

    rows["merchant_case"][1][0] = text("2025")
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert len(batch.merchant_cases) == 2
    assert batch.duplicate_review_facts == ()

    rows["merchant_case"] = [
        [text("2024"), {}, {}, {}, text("Category")],
        [text("2024"), {}, {}, {}, text("Category")],
    ]
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert len(batch.merchant_cases) == 2
    assert batch.duplicate_review_facts == ()


@pytest.mark.parametrize("nda_cell", [checkbox(True, validation=False), checkbox(False, validation=False), {}])
def test_every_nonempty_restricted_row_is_denied_regardless_of_nda(nda_cell):
    rows = full_rows()
    rows["restricted_customer"][0][4] = nda_cell
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    fact = batch.restricted_facts[0]
    assert len(batch.restricted_denylist) == 1
    assert fact.can_quote_externally is False
    assert fact.can_enter_general_staging is False
    assert fact.can_enter_brand_review is False
    assert fact.can_enter_retrieval is False
    assert fact.can_publish is False
    assert fact.identity_term_count == 2


def test_restricted_missing_identity_is_retained_with_stable_reason():
    rows = full_rows()
    rows["restricted_customer"][0][1] = {}
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert len(batch.restricted_denylist) == 1
    assert "RESTRICTED_IDENTITY_MISSING" in batch.restricted_facts[0].reason_codes


def test_public_metric_runs_frozen_minimizer_and_only_exact_missing_met_becomes_staging(monkeypatch):
    called = []
    original = wp3.minimize_public_metric_source

    def observed(source):
        called.append(type(source))
        return original(source)

    monkeypatch.setattr(wp3, "minimize_public_metric_source", observed)
    batch = normalize_coverage_proven_batch(synthetic_context())
    metric = batch.public_metrics[0]
    assert called == [wp3.MetricSourceCells]
    assert type(metric) is PublicMetricStaging
    assert metric.identity_state.value == "SCHEMA_DEFERRED"
    assert metric.review_status is not ReviewStatus.APPROVED
    assert metric.publish_eligibility.value == "needs_review"
    assert metric.can_quote_externally is False
    assert metric.allowed_exposure_channels == (metric.allowed_exposure_channels[0],)
    assert not isinstance(metric, PublicMetric)


def test_public_metric_distinct_evidence_urls_remain_review_without_selecting_winner():
    rows = full_rows()
    rows["public_metric"][0][5] = text(
        "Evidence links",
        textFormatRuns=[
            {"startIndex": 0, "format": {"link": {"uri": "https://one.example/evidence"}}},
            {"startIndex": 5, "format": {"link": {"uri": "https://two.example/evidence"}}},
        ],
    )
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert batch.public_metrics == ()
    assert "PUBLIC_METRIC_MULTIPLE_EVIDENCE_URLS" in {
        code for fact in batch.review_facts for code in fact.reason_codes
    }


def test_public_metric_evidence_does_not_change_statement_or_expand_channels():
    batch = normalize_coverage_proven_batch(synthetic_context())
    metric = batch.public_metrics[0]
    assert metric.statement == "Public claim"
    assert metric.evidence_urls == ("https://evidence.example/report",)
    assert metric.allowed_exposure_channels == (wp3.ExposureChannel.PRESS_RELEASE,)


def test_oral_only_and_note_retention_blocked_payloads_are_irreversibly_excluded():
    for note in ({}, text("只能口頭，不可書面")):
        rows = full_rows()
        rows["public_metric"][0][3] = note
        rows["public_metric"][0][6:13] = [
            checkbox(False), checkbox(False), checkbox(False), checkbox(True),
            checkbox(False), checkbox(False), checkbox(False),
        ]
        batch = normalize_coverage_proven_batch(synthetic_context(rows))
        assert batch.public_metrics == ()
        assert len(batch.excluded_public_metrics) == 1
        assert type(batch.excluded_public_metrics[0]) is ExcludedSourceRef


def test_other_minimizer_failure_is_review_not_schema_deferred_staging():
    rows = full_rows()
    rows["public_metric"][0][6] = checkbox(True, validation=False)
    batch = normalize_coverage_proven_batch(synthetic_context(rows))
    assert batch.public_metrics == ()
    assert "PUBLIC_METRIC_CHANNEL_GOVERNANCE_UNCERTAIN" in {
        code for fact in batch.review_facts for code in fact.reason_codes
    }


def test_public_exposure_booleans_never_truthy_coerce_text_or_numbers():
    for invalid in (text("TRUE"), number(1)):
        rows = full_rows()
        rows["public_metric"][0][6] = invalid
        batch = normalize_coverage_proven_batch(synthetic_context(rows))
        assert batch.public_metrics == ()
        assert {
            code for fact in batch.review_facts for code in fact.reason_codes
        } & {
            "CELL_EFFECTIVE_TYPE_MISMATCH",
            "PUBLIC_METRIC_CHANNEL_GOVERNANCE_UNCERTAIN",
        }


def test_pending_row_three_is_positional_internal_nonquotable_and_not_public():
    batch = normalize_coverage_proven_batch(synthetic_context())
    pending = batch.pending_metrics[0]
    fact = batch.pending_facts[0]
    assert type(pending) is PendingMetricStaging
    assert pending.source_lineage.source_row == 3
    assert pending.lifecycle_status == "DRAFT"
    assert pending.exposure == "INTERNAL_REVIEW_ONLY"
    assert pending.authority == "NON_OFFICIAL"
    assert pending.can_quote_externally is False
    assert pending.can_publish is False
    assert pending.metric_kind == "NOT_PUBLIC_METRIC"
    assert not isinstance(pending, PublicMetricStaging)
    assert fact.can_quote_externally is False


def test_handle_mapping_is_evidence_only_and_does_not_overwrite_merchant_categories():
    batch = normalize_coverage_proven_batch(synthetic_context())
    mapping = batch.handle_mappings[0]
    merchant = batch.merchant_cases[0]
    assert type(mapping) is HandleMappingStaging
    assert mapping.evidence_authority == "EVIDENCE_ONLY"
    assert mapping.category_lv1 == "Suggested"
    assert merchant.sales_category_lv1 == "Retail"
    assert not hasattr(mapping, "brand_id")


def test_exactly_six_batch_schema_deferred_diagnostics_and_no_per_row_ids():
    batch = normalize_coverage_proven_batch(synthetic_context())
    assert len(batch.id_diagnostics) == 6
    assert {item.namespace for item in batch.id_diagnostics} == {
        IdNamespace.MREC, IdNamespace.BRD, IdNamespace.MET, IdNamespace.NONE
    }
    assert {item.reason_code for item in batch.id_diagnostics} == {
        "MERCHANT_MREC_ASSIGNMENT_DEFERRED",
        "MERCHANT_BRD_ASSIGNMENT_DEFERRED",
        "MERCHANT_ID_REVIEW_STATUS_DEFERRED",
        "PUBLIC_METRIC_MET_ASSIGNMENT_DEFERRED",
        "BRAND_ID_MAPPING_AUTHORITY_DEFERRED",
        "BRAND_ID_INITIAL_REVIEW_DEFERRED",
    }
    assert not hasattr(batch.merchant_cases[0], "source_record_id")
    assert not hasattr(batch.merchant_cases[0], "brand_id")
    assert not hasattr(batch.public_metrics[0], "metric_id")


def test_trusted_runtime_objects_are_builder_only_and_final_models_are_absent():
    constructors = (
        CanonicalNormalizationBatch,
        MerchantCaseStaging,
        RestrictedDenylistStaging,
        PublicMetricStaging,
        PendingMetricStaging,
        HandleMappingStaging,
        BrandReviewCandidate,
    )
    for constructor in constructors:
        with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
            constructor()
        with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
            constructor(**{"authority": "trusted"})

    batch = normalize_coverage_proven_batch(synthetic_context())
    all_values = (
        *batch.merchant_cases, *batch.restricted_denylist, *batch.public_metrics,
        *batch.pending_metrics, *batch.handle_mappings,
    )
    assert not any(isinstance(value, (Brand, BrandIdentityDecision, SourceRecord, PublicMetric)) for value in all_values)


def test_arbitrary_object_or_hash_cannot_forge_internal_construction_provenance():
    for forged in (object(), "sha256:" + "0" * 64):
        with pytest.raises(CanonicalNormalizationError, match="CONSTRUCTION_PROVENANCE_INVALID"):
            wp3._new(MerchantCaseStaging, forged, name="forged")
    with pytest.raises(CanonicalNormalizationError, match="PROVENANCE_ISSUANCE_INVALID"):
        wp3._issue_wp3_provenance(object(), object(), object())


def test_sensitive_staging_is_immutable_redacted_nonserializable_and_alias_safe():
    batch = normalize_coverage_proven_batch(synthetic_context())
    merchant = batch.merchant_cases[0]
    lineage = merchant.source_lineage
    lineage.source_columns["merchant_name"] = "Z"

    assert merchant.source_lineage.source_columns["merchant_name"] == "C"
    assert "Merchant Secret" not in repr(merchant)
    assert "merchant-note" not in repr(merchant)
    assert not hasattr(merchant, "model_dump")
    assert not hasattr(merchant, "to_dict")
    with pytest.raises(AttributeError):
        merchant.name = "changed"
    with pytest.raises(TypeError):
        pickle.dumps(merchant)
    with pytest.raises(TypeError):
        json.dumps(merchant)


def test_safe_outputs_exclude_all_sensitive_security_sentinels_and_full_urls():
    batch = normalize_coverage_proven_batch(synthetic_context())
    safe = repr(
        (
            batch.restricted_facts,
            batch.pending_facts,
            batch.brand_review_candidates,
            batch.id_diagnostics,
            batch.review_facts,
            batch.duplicate_review_facts,
            batch.excluded_public_metrics,
        )
    )
    for sentinel in (
        "Merchant Secret", "merchant-note", "RESTRICTED_BRAND_SENTINEL",
        "RESTRICTED_WEBSITE_SENTINEL", "RESTRICTED_NOTE_SENTINEL", "SUBMITTED_BY_SENTINEL",
        "PENDING_BODY_SENTINEL", "PENDING_NOTE_SENTINEL", "https://", "Authorization",
        production_google_sheets_runtime_config().read_plan.spreadsheet_id,
    ):
        assert sentinel not in safe


def test_normalization_has_zero_network_google_or_filesystem_authority(monkeypatch, tmp_path):
    context = synthetic_context()

    def unexpected(*args, **kwargs):
        raise AssertionError("external authority used")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    before = tuple(tmp_path.iterdir())
    batch = normalize_coverage_proven_batch(context)
    assert type(batch) is CanonicalNormalizationBatch
    assert tuple(tmp_path.iterdir()) == before
