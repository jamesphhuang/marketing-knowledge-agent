import ast
import csv
import json
from pathlib import Path

import pytest

from fixtures import write_row_v1_preview_lineage
from marketing_knowledge_agent.review_template import REVIEW_COLUMNS


def test_apply_review_decisions_routes_all_decisions_and_preserves_conservation(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())

    summary = apply_review_decisions(decisions, preview_dir, output_dir)

    assert summary["preflight"]["validation_error_count"] == 0
    assert summary["preflight"]["reviewer_complete"] is True
    assert summary["preflight"]["row_coverage_ok"] is True
    assert summary["bucket_counts"]["approved_vault_preview_md"] == 5
    assert summary["bucket_counts"]["vault_only_md"] == 2
    assert summary["bucket_counts"]["governance_restricted_customers"] == 1
    assert summary["bucket_counts"]["internal_pending_metrics"] == 1
    assert summary["bucket_counts"]["excluded"] == 1
    assert summary["bucket_counts"]["unfinished_review"] == 4
    assert summary["bucket_counts"]["deprecated"] == 1
    assert summary["bucket_counts"]["not_reviewed"] == 2
    assert summary["handle_mapping_count"] == 1
    assert summary["conservation"]["ok"] is True
    assert summary["decision_overrides"]
    assert (output_dir / "approved_vault_preview" / "merchant_cases").exists()
    assert len(list((output_dir / "approved_vault_preview").rglob("*.md"))) == 7
    assert (output_dir / "governance_table_preview" / "restricted_customers.json").exists()
    assert "pending metric" in (output_dir / "internal_inventory_preview" / "pending_metrics.md").read_text(encoding="utf-8")
    excluded = (output_dir / "excluded_records.md").read_text(encoding="utf-8")
    assert "未完成審核" in excluded
    assert "deprecated" in excluded
    assert "本輸出為 preview" in (output_dir / "apply_decisions_summary.md").read_text(encoding="utf-8")


def test_apply_review_decisions_rejects_validation_errors_without_output(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import ApplyReviewDecisionsError, apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    rows = _all_review_rows()
    rows[0]["review_decision"] = "not_allowed"
    _write_decisions(decisions, rows)

    with pytest.raises(ApplyReviewDecisionsError):
        apply_review_decisions(decisions, preview_dir, output_dir)

    assert not output_dir.exists()


def test_apply_review_decisions_rejects_blank_reviewer_without_output(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import ApplyReviewDecisionsError, apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    rows = _all_review_rows()
    rows[0]["reviewer"] = ""
    _write_decisions(decisions, rows)

    with pytest.raises(ApplyReviewDecisionsError, match="人工簽核"):
        apply_review_decisions(decisions, preview_dir, output_dir)

    assert not output_dir.exists()


def test_apply_review_decisions_rejects_row_coverage_mismatch_without_output(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import ApplyReviewDecisionsError, apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows()[:-1])

    with pytest.raises(ApplyReviewDecisionsError, match="row coverage"):
        apply_review_decisions(decisions, preview_dir, output_dir)

    assert not output_dir.exists()


def test_apply_review_decisions_rebuilds_existing_output_idempotently(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old", encoding="utf-8")
    _write_decisions(decisions, _all_review_rows())

    summary = apply_review_decisions(decisions, preview_dir, output_dir)

    assert summary["preflight"]["previous_output_exists"] is True
    assert not (output_dir / "old.txt").exists()
    assert (output_dir / "apply_decisions_summary.md").exists()


def test_apply_review_decisions_isolates_not_reviewed_by_default(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())

    summary = apply_review_decisions(decisions, preview_dir, output_dir)

    assert summary["bucket_counts"]["not_reviewed"] == 2
    assert "Clean Merchant" in (output_dir / "not_reviewed_records.md").read_text(encoding="utf-8")
    vault_text = "\n".join(path.read_text(encoding="utf-8") for path in (output_dir / "approved_vault_preview").rglob("*.md"))
    assert "Clean Merchant" not in vault_text


def test_apply_review_decisions_include_clean_records_promotes_not_reviewed(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())

    summary = apply_review_decisions(decisions, preview_dir, output_dir, include_clean_records=True)

    assert summary["bucket_counts"]["not_reviewed"] == 0
    assert summary["bucket_counts"]["default_policy_approved"] == 2
    vault_text = "\n".join(path.read_text(encoding="utf-8") for path in (output_dir / "approved_vault_preview").rglob("*.md"))
    assert "reviewer: \"default_policy\"" in vault_text


def test_apply_review_decisions_include_clean_merchant_cases_keeps_public_metrics_isolated(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())

    summary = apply_review_decisions(
        decisions,
        preview_dir,
        output_dir,
        include_clean_merchant_cases=True,
    )

    assert summary["bucket_counts"]["not_reviewed"] == 1
    assert summary["bucket_counts"]["default_policy_approved"] == 1
    assert summary["bucket_counts"]["clean_merchant_policy_approved"] == 1
    assert summary["include_clean_merchant_cases"] is True
    vault_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (output_dir / "approved_vault_preview").rglob("*.md")
    )
    not_reviewed = (output_dir / "not_reviewed_records.md").read_text(encoding="utf-8")
    assert "Clean Merchant" in vault_text
    assert "Included by --include-clean-merchant-cases policy." in vault_text
    assert "Clean Metric" not in vault_text
    assert "Clean Metric" in not_reviewed


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"merchant_status": "已關店"}, False),
        ({"merchant_handle": None}, False),
        ({"article_title": None}, False),
        ({"data_classification": "internal"}, False),
        ({"can_quote_externally": False}, False),
        ({"can_enter_content_index": False}, False),
        ({"governance_risk_reasons": ["risk"]}, False),
        ({"invalid_asset_fields": ["文章"]}, False),
        ({"same_brand_multiple_records": True}, False),
        ({"suspected_duplicate_review": True}, False),
    ],
)
def test_clean_merchant_policy_requires_all_safety_conditions(overrides, expected):
    from marketing_knowledge_agent.apply_review_decisions import is_clean_merchant_case_policy_eligible

    record = _merchant_record(80, "Policy Merchant")
    record.update(overrides)

    assert is_clean_merchant_case_policy_eligible(record) is expected


def test_apply_cli_clean_record_flags_are_mutually_exclusive():
    from marketing_knowledge_agent.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "apply-review-decisions",
            "--decisions",
            "decisions.csv",
            "--include-clean-merchant-cases",
        ]
    )

    assert args.include_clean_merchant_cases is True
    assert args.include_clean_records is False
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "apply-review-decisions",
                "--decisions",
                "decisions.csv",
                "--include-clean-records",
                "--include-clean-merchant-cases",
            ]
        )


def test_apply_preview_restricted_whitelist_assertion_fails_before_output(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import ApplyReviewDecisionsError, apply_review_decisions

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview", restricted_brand="Approve Merchant")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())

    with pytest.raises(ApplyReviewDecisionsError, match="restricted"):
        apply_review_decisions(decisions, preview_dir, output_dir)

    assert not output_dir.exists()


def test_apply_preview_pending_metric_whitelist_assertion_fails(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import ApplyReviewDecisionsError, assert_apply_preview_safety

    output_dir = tmp_path / "apply_preview"
    pending_file = output_dir / "approved_vault_preview" / "pending_metrics" / "pending.md"
    pending_file.parent.mkdir(parents=True)
    pending_file.write_text("---\nrecord_type: \"pending_metric\"\n---\n", encoding="utf-8")

    with pytest.raises(ApplyReviewDecisionsError, match="pending_metric"):
        assert_apply_preview_safety(output_dir, restricted_customers=[])


def test_apply_preview_safety_ignores_appledouble_metadata_files(tmp_path):
    from marketing_knowledge_agent.apply_review_decisions import assert_apply_preview_safety

    output_dir = tmp_path / "apply_preview"
    preview_dir = output_dir / "approved_vault_preview" / "merchant_cases"
    preview_dir.mkdir(parents=True)
    (preview_dir / "record.md").write_text("---\nrecord_type: \"merchant_case\"\n---\n", encoding="utf-8")
    (preview_dir / "._record.md").write_bytes(b"\xb0\x00\x00AppleDouble")

    assert_apply_preview_safety(output_dir, restricted_customers=[])


def test_apply_preview_writes_only_under_output_dir(tmp_path, monkeypatch):
    import marketing_knowledge_agent.apply_review_decisions as apply_module

    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = tmp_path / "decisions.csv"
    output_dir = tmp_path / "apply_preview"
    _write_decisions(decisions, _all_review_rows())
    written_paths = []
    original_write_text = apply_module._write_text
    original_write_json = apply_module._write_json

    def recording_write_text(output_root, relative_path, content):
        path = original_write_text(output_root, relative_path, content)
        written_paths.append(path.resolve())
        return path

    def recording_write_json(output_root, relative_path, payload):
        path = original_write_json(output_root, relative_path, payload)
        written_paths.append(path.resolve())
        return path

    monkeypatch.setattr(apply_module, "_write_text", recording_write_text)
    monkeypatch.setattr(apply_module, "_write_json", recording_write_json)

    apply_module.apply_review_decisions(decisions, preview_dir, output_dir)

    assert written_paths
    assert all(_is_relative_to(path, output_dir.resolve()) for path in written_paths)


def test_apply_preview_does_not_import_indexing():
    source = Path("src/marketing_knowledge_agent/apply_review_decisions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

    assert "marketing_knowledge_agent.indexing" not in imports
    assert "indexing" not in imports
    assert "SQLiteIndex" not in source


def _write_apply_preview_fixture(preview_dir: Path, restricted_brand: str = "Restricted Customer") -> Path:
    preview_dir.mkdir(parents=True, exist_ok=True)
    merchant_cases = [
        _merchant_record(1, "Approve Merchant", merchant_handle=None),
        _merchant_record(2, "Internal Merchant", governance_risk_reasons=["risk"], can_quote_externally=False),
        _merchant_record(3, "Multi Merchant", same_brand_multiple_records=True),
        _merchant_record(4, "Restricted Use Merchant", governance_risk_reasons=["restricted"], can_quote_externally=False),
        _merchant_record(5, "Excluded Merchant", governance_risk_reasons=["exclude"], can_quote_externally=False),
        _merchant_record(6, "Vault Only Merchant", no_valid_content_asset=True, can_enter_content_index=False, can_quote_externally=False),
        _merchant_record(7, "Internal Vault Only Merchant", merchant_handle=None),
        _merchant_record(
            8,
            "Needs Update Merchant",
            governance_issue_types=["invalid_asset_value"],
            invalid_asset_fields=["文章"],
            invalid_asset_values={"文章": "審核中"},
        ),
        _merchant_record(9, "Enrich Merchant", merchant_handle=None),
        _merchant_record(10, "Manual Merchant", suspected_duplicate_review=True),
        _merchant_record(11, "Identity Merchant", merchant_handle=None),
        _merchant_record(12, "Deprecated Merchant", governance_risk_reasons=["deprecated"], can_quote_externally=False),
        _merchant_record(13, "Clean Merchant", merchant_handle="clean-merchant"),
    ]
    public_metrics = [
        _public_metric_record(20, "Restricted Use Metric", restricted_note="verbal only"),
        _public_metric_record(21, "Clean Metric", restricted_note=None),
    ]
    pending_metrics = [_pending_metric_record(30)]
    restricted_customers = [_restricted_customer_record(40, restricted_brand)]
    handle_mappings = [{"record_type": "handle_mapping", "source_sheet": "handle 比對", "source_row": 50}]
    for filename, payload in {
        "merchant_cases.json": merchant_cases,
        "public_metrics.json": public_metrics,
        "pending_metrics.json": pending_metrics,
        "restricted_customers.json": restricted_customers,
        "handle_mappings.json": handle_mappings,
    }.items():
        (preview_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_row_v1_preview_lineage(preview_dir)
    return preview_dir


def _all_review_rows():
    return [
        _decision_row("商家夥伴案例資料庫", 1, "merchant_case", "approve", can_enter_vault="true"),
        _decision_row("商家夥伴案例資料庫", 2, "merchant_case", "approve_internal_only", can_quote_externally="false"),
        _decision_row("商家夥伴案例資料庫", 3, "merchant_case", "keep_all_records"),
        _decision_row("商家夥伴案例資料庫", 4, "merchant_case", "restricted_use_only", can_quote_externally="false", allowed_exposure_channels="saleskits"),
        _decision_row("商家夥伴案例資料庫", 5, "merchant_case", "exclude", can_enter_content_index="false", can_quote_externally="false"),
        _decision_row("商家夥伴案例資料庫", 6, "merchant_case", "exclude_from_content_index", can_enter_content_index="false", can_quote_externally="false"),
        _decision_row("商家夥伴案例資料庫", 7, "merchant_case", "keep_internal_only", can_enter_content_index="false", can_quote_externally="false", final_status="internal_only"),
        _decision_row("商家夥伴案例資料庫", 8, "merchant_case", "needs_update", final_status="review_required"),
        _decision_row("商家夥伴案例資料庫", 9, "merchant_case", "enrich_metadata", final_status="review_required"),
        _decision_row("商家夥伴案例資料庫", 10, "merchant_case", "manual_review", final_status="review_required"),
        _decision_row("商家夥伴案例資料庫", 11, "merchant_case", "review_identity_mapping", final_status="review_required"),
        _decision_row("商家夥伴案例資料庫", 12, "merchant_case", "deprecated", can_quote_externally="false", final_status="excluded"),
        _decision_row("「可公開」對外數據", 20, "public_metric", "restricted_use_only", allowed_exposure_channels="saleskits"),
        _decision_row("待確認數據", 30, "pending_metric", "keep_internal_only", can_enter_vault="false", can_enter_content_index="false", can_quote_externally="false", final_status="pending_review"),
        _decision_row("「不可公開」客戶名單", 40, "restricted_customer", "enter_governance_table_only", can_enter_vault="false", can_enter_content_index="false", can_enter_governance_table="true", can_quote_externally="false", final_status="restricted"),
    ]


def _decision_row(
    source_sheet,
    source_row,
    record_type,
    decision,
    can_enter_vault="true",
    can_enter_content_index="true",
    can_enter_governance_table="false",
    can_quote_externally="true",
    allowed_exposure_channels="",
    final_status="approved",
):
    row = {column: "" for column in REVIEW_COLUMNS}
    row.update(
        {
            "source_sheet": source_sheet,
            "source_row": str(source_row),
            "record_type": record_type,
            "brand_name": "Restricted Brand" if source_row == 1 else f"Brand {source_row}",
            "metric_name": f"Metric {source_row}" if record_type == "public_metric" else "",
            "title": f"Title {source_row}",
            "suggested_action": decision,
            "review_decision": decision,
            "can_enter_vault": can_enter_vault,
            "can_enter_content_index": can_enter_content_index,
            "can_enter_governance_table": can_enter_governance_table,
            "can_quote_externally": can_quote_externally,
            "allowed_exposure_channels": allowed_exposure_channels,
            "final_status": final_status,
            "reviewer": "Reviewer",
            "reviewed_at": "2026-07-10",
            "notes": "review note",
        }
    )
    if source_row == 6:
        row["issue_type"] = "no_valid_content_asset"
    if source_row == 3:
        row["issue_type"] = "same_brand_multiple_records"
    return row


def _write_decisions(path: Path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REVIEW_COLUMNS})


def _base_record(source_row, record_type, title):
    return {
        "title": title,
        "source_type": "database",
        "record_type": record_type,
        "status": "published",
        "publish_date": "2026-07-01",
        "captured_date": "2026-07-01",
        "source_sheet": _sheet_for(record_type),
        "source_row": source_row,
        "data_classification": "public",
        "can_quote_externally": True,
        "can_enter_content_index": True,
    }


def _merchant_record(source_row, brand_name, **overrides):
    payload = _base_record(source_row, "merchant_case", brand_name)
    payload.update(
        {
            "brand_name": brand_name,
            "merchant_handle": f"handle-{source_row}",
            "merchant_status": "現有商家",
            "article_title": f"{brand_name} article",
            "video_title": None,
            "podcast_title": None,
            "news_title": None,
            "notes": "merchant note",
        }
    )
    payload.update(overrides)
    return payload


def _public_metric_record(source_row, metric_name, restricted_note):
    payload = _base_record(source_row, "public_metric", metric_name)
    payload.update(
        {
            "metric_name": metric_name,
            "claim_statement": f"{metric_name} claim",
            "metric_note": "metric note",
            "allowed_exposure_channels": ["saleskits"],
            "restricted_note": restricted_note,
        }
    )
    return payload


def _pending_metric_record(source_row):
    payload = _base_record(source_row, "pending_metric", "Example pending metric")
    payload.update(
        {
            "metric_name": "Example pending metric",
            "claim_statement": "pending metric claim",
            "claim_status": "pending_review",
            "data_classification": "internal",
            "can_quote_externally": False,
            "can_enter_content_index": False,
        }
    )
    return payload


def _restricted_customer_record(source_row, brand_name):
    payload = _base_record(source_row, "restricted_customer", brand_name)
    payload.update(
        {
            "brand_name": brand_name,
            "website_url": "https://restricted.example",
            "restricted_aliases": [brand_name],
            "restricted_reason": "不可公開客戶名單",
            "data_classification": "restricted",
            "can_quote_externally": False,
            "can_enter_content_index": False,
        }
    )
    return payload


def _sheet_for(record_type):
    return {
        "merchant_case": "商家夥伴案例資料庫",
        "public_metric": "「可公開」對外數據",
        "pending_metric": "待確認數據",
        "restricted_customer": "「不可公開」客戶名單",
    }[record_type]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
